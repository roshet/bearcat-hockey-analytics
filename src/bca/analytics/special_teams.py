"""Run the penalty-state simulator over every simulable game, and calibrate it.

A game is simulable when its box score is full and every goal on the board
has a goal event (``game_quality.goal_events``): score-only and partly
entered games would produce confident nonsense.

Calibration (``bca special-teams validate``) is the M2 discipline applied to
the simulator's open rule choices. The league's scorekeepers flag each
penalty ``isPowerPlay`` and each goal PP/SH; their PP-opportunity field is
simply the count of the opponent's flagged penalties (96.4% exact across
10,026 team-games). Those flags are human judgments, not ground truth -- one
flags a game misconduct -- so the rule set that agrees with them most often
becomes the default, and the disagreements are read, not tuned away.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import product

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.penalty_state import (
    OVERTIME_SECONDS,
    REGULATION_END,
    GameInput,
    GameStrength,
    Goal,
    Penalty,
    Rules,
    game_time,
    simulate,
)
from bca.db import schema as db
from bca.ingest.parsers import game_summary as gs


@dataclass(frozen=True)
class Flags:
    """What the scorekeeper recorded, to compare against."""

    #: penalty_id -> isPowerPlay
    penalties: dict[int, bool]
    #: goal key (game, goal) -> "pp" / "sh" / "ev"
    goals: dict[tuple[int, int], str]
    #: (game, team) -> entered PP opportunities, where game_quality says entered
    opportunities: dict[tuple[int, int], int]


@dataclass
class League:
    games: list[GameInput]
    flags: Flags
    skipped: Counter[str] = field(default_factory=Counter)


def load(session: Session) -> League:
    quality: dict[int, list[db.GameQuality]] = defaultdict(list)
    for q in session.scalars(select(db.GameQuality)):
        quality[q.game_id].append(q)

    games = {
        g.game_id: g
        for g in session.scalars(
            select(db.Game).where(db.Game.is_final, db.Game.summary_normalized)
        )
    }
    skipped: Counter[str] = Counter()
    simulable = set()
    for game_id, game in games.items():
        rows = quality.get(game_id, [])
        if game.box_score_kind != "full":
            skipped["score-only box score"] += 1
        elif not rows or not all(q.goal_events for q in rows):
            skipped["partly entered box score"] += 1
        else:
            simulable.add(game_id)

    penalties: dict[int, list[Penalty]] = defaultdict(list)
    penalty_flags: dict[int, bool] = {}
    p = db.PenaltyEvent
    for row in session.scalars(select(p).order_by(p.game_id, p.penalty_id)):
        if row.game_id not in simulable:
            continue
        penalties[row.game_id].append(Penalty(
            penalty_id=row.penalty_id,
            time=game_time(row.period, row.seconds),
            team_id=row.team_id,
            minutes=row.minutes,
            description=row.description,
            player_id=row.taken_by_id or row.served_by_id,
        ))
        penalty_flags[row.penalty_id] = row.upstream_is_power_play

    goals: dict[int, list[Goal]] = defaultdict(list)
    goal_flags: dict[tuple[int, int], str] = {}
    e = db.GoalEvent
    for event in session.scalars(select(e).order_by(e.game_id, e.goal_id)):
        if event.game_id not in simulable or event.seconds is None:
            continue
        goals[event.game_id].append(Goal(
            event.goal_id, game_time(event.period, event.seconds), event.team_id,
            event.is_empty_net,
        ))
        goal_flags[(event.game_id, event.goal_id)] = (
            "pp" if event.is_power_play else "sh" if event.is_short_handed else "ev"
        )

    opportunities = {
        (s.game_id, s.team_id): s.pp_opportunities
        for s in session.scalars(select(db.GameTeamStats))
        if s.game_id in simulable
        and any(q.team_id == s.team_id and q.pp_opportunities for q in quality[s.game_id])
    }

    inputs = []
    for game_id in sorted(simulable):
        game = games[game_id]
        inputs.append(GameInput(
            game_id=game_id,
            home_team_id=game.home_team_id,
            visiting_team_id=game.visiting_team_id,
            penalties=tuple(penalties[game_id]),
            goals=tuple(goals[game_id]),
            end_time=end_time(game.overtime, game.shootout, goals[game_id]),
        ))
    return League(inputs, Flags(penalty_flags, goal_flags, opportunities), skipped)


def end_time(overtime: bool, shootout: bool, goals: list[Goal]) -> int:
    """Regulation; or overtime, which ends at its winning goal or after 5:00."""
    if not (overtime or shootout):
        return REGULATION_END
    overtime_goals = [g.time for g in goals if g.time > REGULATION_END]
    if shootout or not overtime_goals:
        return REGULATION_END + OVERTIME_SECONDS
    # Past 5:00 is reported by the simulator as an event after play ended.
    return min(max(overtime_goals), REGULATION_END + OVERTIME_SECONDS)


def from_box_score(summary: gs.GameSummary) -> GameInput:
    """A GameInput straight from a parsed box score -- for golden tests on fixtures."""
    goals = [
        Goal(g.goal_id, game_time(g.period.number, g.seconds), g.team.team_id,
             g.properties.is_empty_net)
        for g in summary.goals
        if g.seconds is not None
    ]
    def served_by(p: gs.Penalty) -> int | None:
        person = p.taken_by or p.served_by
        return person.player_id if person is not None else None

    penalties = tuple(
        Penalty(
            penalty_id=p.penalty_id,
            time=game_time(p.period.number, p.seconds),
            team_id=p.team.team_id,
            minutes=p.minutes,
            description=p.description,
            player_id=served_by(p),
        )
        for p in summary.penalties
    )
    overtime = any(period.period.number > 3 for period in summary.periods)
    return GameInput(
        game_id=summary.details.game_id,
        home_team_id=summary.home.info.team_id,
        visiting_team_id=summary.visiting.info.team_id,
        penalties=penalties,
        goals=tuple(goals),
        end_time=end_time(overtime, summary.has_shootout, goals),
    )


# --- calibration ----------------------------------------------------------------


@dataclass
class Agreement:
    """How one rule set's simulation compares with the scorekeepers' flags.

    Penalties are compared per team per instant (how many of a team's
    penalties at one stoppage gave the opponent an advantage vs how many the
    scorekeeper flagged), because *which* of two equal minors survives a
    cancellation is arbitrary. Only team-games whose PP data was entered at
    all count: elsewhere every flag is simply false.
    """

    rules: Rules
    #: "agree" / "simulated more" / "flagged more" -> team-instants
    penalty_instants: Counter[str]
    #: (simulated situation, flagged situation) -> goals
    goals: Counter[tuple[str, str]]
    #: entered minus derived opportunities -> team-games
    opportunity_diff: Counter[int]

    @property
    def penalty_rate(self) -> float:
        return self.penalty_instants["agree"] / (sum(self.penalty_instants.values()) or 1)

    @property
    def goal_rate(self) -> float:
        total = sum(self.goals.values()) or 1
        return sum(n for (a, b), n in self.goals.items() if a == b) / total

    @property
    def opportunity_exact(self) -> float:
        return self.opportunity_diff[0] / (sum(self.opportunity_diff.values()) or 1)

    @property
    def opportunity_within_one(self) -> float:
        near = sum(n for d, n in self.opportunity_diff.items() if abs(d) <= 1)
        return near / (sum(self.opportunity_diff.values()) or 1)


def agreement(league: League, rules: Rules) -> tuple[Agreement, list[GameStrength]]:
    results = [simulate(game, rules) for game in league.games]
    by_id = {game.game_id: game for game in league.games}
    instants: Counter[str] = Counter()
    goals: Counter[tuple[str, str]] = Counter()
    diff: Counter[int] = Counter()
    for result in results:
        game = by_id[result.game_id]
        simulated: Counter[tuple[int, int]] = Counter()
        flagged: Counter[tuple[int, int]] = Counter()
        for p in game.penalties:
            if (result.game_id, game.opponent(p.team_id)) not in league.flags.opportunities:
                continue
            outcome = result.penalties.get(p.penalty_id)
            simulated[(p.team_id, p.time)] += bool(outcome and outcome.created_advantage)
            flagged[(p.team_id, p.time)] += league.flags.penalties[p.penalty_id]
        for key in simulated.keys() | flagged.keys():
            s, f = simulated[key], flagged[key]
            instants["agree" if s == f else "simulated more" if s > f else "flagged more"] += 1
        for g in result.goals:
            flag = league.flags.goals.get((result.game_id, g.goal_id))
            if flag is not None:
                goals[(g.situation, flag)] += 1
        for team_id, team in result.teams.items():
            entered = league.flags.opportunities.get((result.game_id, team_id))
            if entered is not None:
                diff[entered - team.pp_opportunities] += 1
    return Agreement(rules, instants, goals, diff), results


# --- the league-wide run --------------------------------------------------------


@dataclass
class SpecialTeamsRun:
    games: int
    skipped: Counter[str]
    issues: list[tuple[int, str]]
    agreement: Agreement


def run_special_teams(
    sessions: sessionmaker[Session], rules: Rules | None = None
) -> SpecialTeamsRun:
    """Simulate every simulable game and replace the three special-teams tables."""
    rules = rules or Rules()
    with sessions.begin() as session:
        league = load(session)
        result, results = agreement(league, rules)
        for table in (db.TeamGameSpecialTeams, db.GoalStrengthPenalty, db.GoalStrength,
                      db.PenaltyOutcome):
            session.execute(delete(table))
        issues: list[tuple[int, str]] = []
        for r in results:
            issues += [(r.game_id, issue) for issue in r.issues]
            for team in r.teams.values():
                session.add(db.TeamGameSpecialTeams(
                    game_id=r.game_id, team_id=team.team_id,
                    pp_opportunities=team.pp_opportunities, pp_seconds=team.pp_seconds,
                    pk_seconds=team.pk_seconds, pp_goals=team.pp_goals, sh_goals=team.sh_goals,
                    pp_goals_against=team.pp_goals_against,
                    sh_goals_against=team.sh_goals_against,
                    entered_pp_opportunities=league.flags.opportunities.get(
                        (r.game_id, team.team_id)
                    ),
                ))
            for g in r.goals:
                session.add(db.GoalStrength(
                    game_id=r.game_id, goal_id=g.goal_id, team_id=g.team_id,
                    situation=g.situation, skaters_for=g.skaters_for,
                    skaters_against=g.skaters_against,
                    flagged_situation=league.flags.goals[(r.game_id, g.goal_id)],
                ))
                session.add_all(
                    db.GoalStrengthPenalty(game_id=r.game_id, goal_id=g.goal_id, penalty_id=pid)
                    for pid in dict.fromkeys(g.serving_penalty_ids)
                )
            for o in r.penalties.values():
                session.add(db.PenaltyOutcome(
                    game_id=r.game_id, penalty_id=o.penalty_id, team_id=o.team_id,
                    cancelled=o.cancelled, delayed=o.delayed,
                    advantage_seconds=o.advantage_seconds,
                    flagged_power_play=league.flags.penalties[o.penalty_id],
                ))
    return SpecialTeamsRun(len(results), league.skipped, issues, result)


def rule_grid() -> list[Rules]:
    return [
        Rules(coincidental=c, coincidental_four_on_four=f, opportunity=o,
              double_minor_opportunities=d)
        for c, f, o, d in product(
            ("like_for_like", "by_time"), (False, True),
            ("per_penalty", "per_advantage", "per_episode"), (1, 2),
        )
        if not (o != "per_penalty" and d == 2)
    ]
