"""``bca audit report``: the M2 gate report, read from the stored audit tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bca.audit.completeness import FIELDS
from bca.audit.reconcile import UNEXPLAINED
from bca.db import schema as db

#: League-wide share of team-games a field must be entered in to be compared
#: across teams without per-game flags. Proposed in the M2 plan; the gate
#: decision itself is made with the user.
SAFE_TO_COMPARE = 0.95
_SAMPLE = 8


@dataclass
class CheckSummary:
    check: str
    unit: str
    checked: int
    not_checkable: int
    findings: int
    by_explanation: dict[str, int]
    sample_unexplained: list[str] = field(default_factory=list)

    @property
    def unexplained(self) -> int:
        return self.by_explanation.get(UNEXPLAINED, 0)

    @property
    def unexplained_rate(self) -> float:
        return self.unexplained / self.checked if self.checked else 0.0


@dataclass
class FieldCompleteness:
    field: str
    entered: int
    not_entered: int
    unknown: int

    @property
    def rate(self) -> float:
        known = self.entered + self.not_entered
        return self.entered / known if known else 0.0


@dataclass
class AuditReport:
    checks: list[CheckSummary]
    completeness: list[FieldCompleteness]
    #: season_id -> field -> entered rate (full and score-only box scores alike).
    completeness_by_season: dict[int, dict[str, float]]
    #: field -> the home teams (scorer's box) with the lowest entered rate.
    weakest_home_teams: dict[str, list[dict[str, Any]]]
    named_cases: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for check, raw in zip(self.checks, data["checks"], strict=True):
            raw["unexplained_rate"] = round(check.unexplained_rate, 5)
        for f, raw in zip(self.completeness, data["completeness"], strict=True):
            raw["rate"] = round(f.rate, 4)
            raw["safe_to_compare"] = f.rate >= SAFE_TO_COMPARE
        return data


def build_report(session: Session) -> AuditReport:
    return AuditReport(
        checks=_checks(session),
        completeness=_completeness(session),
        completeness_by_season=_completeness_by_season(session),
        weakest_home_teams=_weakest_home_teams(session),
        named_cases=named_cases(session),
    )


def _checks(session: Session) -> list[CheckSummary]:
    f = db.ReconciliationFinding
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for check, explanation, n in session.execute(
        select(f.check_name, f.explanation, func.count()).group_by(f.check_name, f.explanation)
    ):
        counts[check][explanation] = n

    summaries = []
    totals = session.scalars(select(db.AuditCheckTotal).order_by(db.AuditCheckTotal.check_name))
    for total in totals:
        by_explanation = counts.get(total.check_name, {})
        sample = [
            _locate(row)
            for row in session.scalars(
                select(f)
                .where(f.check_name == total.check_name, f.explanation == UNEXPLAINED)
                .order_by(f.finding_id)
                .limit(_SAMPLE)
            )
        ]
        summaries.append(CheckSummary(
            check=total.check_name,
            unit=total.unit,
            checked=total.checked,
            not_checkable=total.not_checkable,
            findings=sum(by_explanation.values()),
            by_explanation=dict(sorted(by_explanation.items())),
            sample_unexplained=sample,
        ))
    return summaries


def _locate(row: db.ReconciliationFinding) -> str:
    where = f"game {row.game_id}" if row.game_id else f"season {row.season_id}"
    who = f" team {row.team_id}" if row.team_id else ""
    player = f" player {row.player_id}" if row.player_id else ""
    return f"{where}{who}{player}: expected {row.expected}, got {row.actual}"


def _completeness(session: Session) -> list[FieldCompleteness]:
    q = db.GameQuality
    out = []
    for name in FIELDS:
        column = getattr(q, name)
        entered = session.scalar(select(func.count()).where(column.is_(True))) or 0
        not_entered = session.scalar(select(func.count()).where(column.is_(False))) or 0
        unknown = session.scalar(select(func.count()).where(column.is_(None))) or 0
        out.append(FieldCompleteness(name, entered, not_entered, unknown))
    return out


def _completeness_by_season(session: Session) -> dict[int, dict[str, float]]:
    q = db.GameQuality
    result: dict[int, dict[str, float]] = {}
    for name in FIELDS:
        column = getattr(q, name)
        for season_id, entered, known in session.execute(
            select(
                db.Game.season_id,
                func.count().filter(column.is_(True)),
                func.count(column),
            )
            .join(db.Game, db.Game.game_id == q.game_id)
            .group_by(db.Game.season_id)
        ):
            result.setdefault(season_id, {})[name] = round(entered / known, 4) if known else 0.0
    return dict(sorted(result.items()))


def _weakest_home_teams(
    session: Session, fields: tuple[str, ...] = ("pp_opportunities", "team_shots", "lineup"),
    minimum_games: int = 20, worst: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    """Per field, the home sides (whose volunteers keep the book) entering it least."""
    q = db.GameQuality
    result = {}
    for name in fields:
        column = getattr(q, name)
        rows = session.execute(
            select(
                db.Game.home_team_id,
                func.count().filter(column.is_(True)),
                func.count(column),
            )
            .join(db.Game, db.Game.game_id == q.game_id)
            .group_by(db.Game.home_team_id)
        ).all()
        names = dict(session.execute(select(db.Team.team_id, db.Team.name)).all())
        ranked = sorted(
            ((entered / known, -known, team) for team, entered, known in rows
             if known >= minimum_games)
        )
        result[name] = [
            {"team_id": team, "team": names.get(team, str(team)),
             "team_games": -negative_known, "rate": round(rate, 3)}
            for rate, negative_known, team in ranked[:worst]
        ]
    return result


# --- the three cases the M2 exit criterion names -----------------------------------

UC, CURRENT_SEASON = 265, 73


def named_cases(session: Session) -> dict[str, Any]:
    f = db.ReconciliationFinding
    g = db.Game

    uc_games = select(g.game_id).where(
        g.season_id == CURRENT_SEASON,
        g.is_final,
        (g.home_team_id == UC) | (g.visiting_team_id == UC),
    )
    games = session.scalars(select(g).where(g.game_id.in_(uc_games))).all()
    game_rows_ga = sum(x.visiting_goals if x.home_team_id == UC else x.home_goals for x in games)
    stint_ga = session.scalar(
        select(func.sum(db.GoalieAppearance.goals_against)).where(
            db.GoalieAppearance.game_id.in_(uc_games), db.GoalieAppearance.team_id == UC
        )
    ) or 0
    empty_net_against = session.scalar(
        select(func.count()).select_from(db.GoalEvent).where(
            db.GoalEvent.game_id.in_(uc_games), db.GoalEvent.team_id != UC,
            db.GoalEvent.is_empty_net,
        )
    ) or 0
    u = db.UpstreamPlayerSeasonStats
    upstream = session.scalars(
        select(u).where(u.season_id == CURRENT_SEASON, u.team_id == UC, u.kind == "goalie")
    ).all()
    only_upstream = session.scalars(
        select(f).where(
            f.check_name == "upstream_goalie_ga", f.season_id == CURRENT_SEASON, f.team_id == UC,
            f.explanation == "upstream_only_player",
        )
    ).all()
    names = {x.player_id: x.name for x in upstream}

    def finding(check: str, game_id: int) -> dict[str, Any] | None:
        row = session.scalars(
            select(f).where(f.check_name == check, f.game_id == game_id).order_by(f.finding_id)
        ).first()
        if row is None:
            return None
        return {"team_id": row.team_id, "expected": row.expected, "actual": row.actual,
                "explanation": row.explanation, "detail": row.detail}

    return {
        "uc_2026_27_goals_against": {
            "game_rows": game_rows_ga,
            "box_score_goalies": stint_ga,
            "empty_net_goals_against": empty_net_against,
            "upstream_goalie_lines": sum(x.goals_against or 0 for x in upstream),
            "upstream_only_goalies": [
                {"player_id": x.player_id, "name": names.get(x.player_id or 0, ""),
                 "goals_against": x.actual}
                for x in only_upstream
            ],
        },
        "game_33170_pp_goal_on_zero_opportunities": finding(
            "pp_goals_within_opportunities", 33170
        ),
        "game_31980_goalie_ga": finding("goalie_ga_matches", 31980),
    }
