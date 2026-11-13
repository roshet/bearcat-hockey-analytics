"""Scorekeeping completeness: which optional parts of each box score were entered.

The ACHA's numbers are typed in by volunteers at the home rink, so what is
"recorded" varies game by game. This measures it per game and team, so that

- the UI can flag a game whose numbers cannot be trusted for a given metric, and
- a metric is compared across teams only if its inputs are entered nearly
  everywhere (the M2 gate).

Officials (referees, linesmen, scorekeepers) are not measured: they are empty in
every one of the 6,408 MD1 box scores from 2020-21 to 2026-27.
"""

from __future__ import annotations

from dataclasses import dataclass

from bca.audit.facts import Facts, GoalTotals, SkaterTotals
from bca.db import schema as db

#: The flags, in report order.
FIELDS = (
    "lineup",
    "goal_events",
    "goal_times",
    "assists",
    "team_shots",
    "period_shots",
    "penalties",
    "pp_opportunities",
    "goalies",
    "attendance",
)


@dataclass(frozen=True)
class QualityRow:
    game_id: int
    team_id: int
    is_home: bool
    lineup: bool
    goal_events: bool
    goal_times: bool
    assists: bool | None
    team_shots: bool
    period_shots: bool
    penalties: bool
    pp_opportunities: bool | None
    goalies: bool
    attendance: bool

    def to_model(self) -> db.GameQuality:
        return db.GameQuality(**{k: getattr(self, k) for k in ("game_id", "team_id", "is_home",
                                                               *FIELDS)})


def assess(facts: Facts) -> list[QualityRow]:
    rows = []
    for game in facts.games.values():
        attendance = game.attendance is not None
        game_penalties = any(
            (game.game_id, t) in facts.penalty_minutes for t in game.teams()
        )
        partial = facts.is_partial(game)
        for team in game.teams():
            key = (game.game_id, team)
            is_home = team == game.home_team_id
            if not game.is_full:
                rows.append(QualityRow(
                    game.game_id, team, is_home, lineup=False, goal_events=False,
                    goal_times=False,
                    assists=False, team_shots=False, period_shots=False, penalties=False,
                    pp_opportunities=False, goalies=False, attendance=attendance,
                ))
                continue

            goals = facts.goals.get(key, GoalTotals())
            stats = facts.team_stats.get(key)
            _, home_shots, _, visiting_shots = facts.periods.get(game.game_id, (0, 0, 0, 0))
            period_shots = home_shots if is_home else visiting_shots

            if goals.assists:
                assists: bool | None = True
            elif goals.goals >= 2:
                assists = False  # two or more goals, not one assist: not entered
            else:
                assists = None  # 0 or 1 unassisted goal is plausible

            opps = stats.pp_opportunities if stats else 0
            opponent_minors = facts.advantage_penalties.get(
                (game.game_id, game.opponent(team)), 0
            )
            if opps > 0:
                pp: bool | None = True
            elif opponent_minors or (stats and stats.pp_goals):
                pp = False  # 0 recorded, yet the opponent went short-handed
            else:
                pp = None

            rows.append(QualityRow(
                game.game_id,
                team,
                is_home,
                lineup=facts.skaters.get(key, SkaterTotals()).lines > 0,
                goal_events=not partial,
                goal_times=goals.untimed == 0,
                assists=assists,
                team_shots=bool(stats and stats.shots > 0),
                period_shots=period_shots > 0,
                penalties=game_penalties,
                pp_opportunities=pp,
                goalies=bool(facts.stints.get(key)),
                attendance=attendance,
            ))
    return rows
