"""Per-game, per-team facts gathered once, for the reconciliation and completeness passes.

A handful of grouped queries instead of one query per game: the whole league
is ~6,400 games, so this fits comfortably in memory and keeps each check a
few readable lines of Python rather than a page of SQL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from bca.db import schema as db

#: (game_id, team_id)
TeamGame = tuple[int, int]

#: Penalty lengths that put a team short-handed. Misconducts (10) and game
#: misconducts do not; M3 does this properly, this is only for flagging.
_MAN_ADVANTAGE_MINUTES = (2, 4, 5)


@dataclass
class GameFacts:
    game_id: int
    season_id: int
    home_team_id: int
    visiting_team_id: int
    home_goals: int
    visiting_goals: int
    shootout: bool
    box_score_kind: str | None
    attendance: int | None

    @property
    def is_full(self) -> bool:
        return self.box_score_kind == "full"

    def teams(self) -> tuple[int, int]:
        return (self.home_team_id, self.visiting_team_id)

    def opponent(self, team_id: int) -> int:
        return self.visiting_team_id if team_id == self.home_team_id else self.home_team_id

    def goals_for(self, team_id: int) -> int:
        return self.home_goals if team_id == self.home_team_id else self.visiting_goals

    def shootout_credit(self, team_id: int) -> int:
        """The +1 a shootout winner gets on the scoreboard, which no goal event backs."""
        if not self.shootout:
            return 0
        return int(self.goals_for(team_id) > self.goals_for(self.opponent(team_id)))


@dataclass
class TeamStats:
    shots: int
    goals: int
    pp_goals: int
    pp_opportunities: int
    penalty_minutes: int


@dataclass
class GoalTotals:
    goals: int = 0
    empty_net: int = 0
    power_play: int = 0
    untimed: int = 0
    with_scorer: int = 0
    assists: int = 0


@dataclass
class SkaterTotals:
    lines: int = 0
    goals: int = 0
    assists: int = 0
    penalty_minutes: int = 0


@dataclass
class Stint:
    player_id: int
    shots_against: int
    goals_against: int
    saves: int
    #: When the goalie left the net: (period, seconds elapsed).
    out: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class GoalTime:
    period: int
    seconds: int | None
    is_empty_net: bool


@dataclass
class Facts:
    games: dict[int, GameFacts]
    team_stats: dict[TeamGame, TeamStats]
    goals: dict[TeamGame, GoalTotals]
    skaters: dict[TeamGame, SkaterTotals]
    stints: dict[TeamGame, list[Stint]]
    penalty_minutes: dict[TeamGame, int]
    #: Penalties of a length that normally creates a man advantage.
    advantage_penalties: dict[TeamGame, int]
    #: game_id -> (home goals, home shots, visiting goals, visiting shots) over periods.
    periods: dict[int, tuple[int, int, int, int]]
    #: Box-score appearances: (season, team) -> player ids (skater lines + goalie stints).
    appearances: dict[tuple[int, int], set[int]]
    #: Roster player ids per (season, team), for MD1 team-seasons whose roster is loaded.
    rosters: dict[tuple[int, int], set[int]]
    md1: set[tuple[int, int]]
    #: (season, team, player) -> totals over that player's skater lines (goalies excluded).
    skater_seasons: dict[tuple[int, int, int], SkaterTotals] = field(default_factory=dict)
    upstream: list[db.UpstreamPlayerSeasonStats] = field(default_factory=list)
    #: Each team's goals, with time and empty-net flag.
    goal_times: dict[TeamGame, list[GoalTime]] = field(default_factory=dict)
    #: Assists on a team's goals credited to its goalie (who has no skater line).
    goalie_assists: dict[TeamGame, int] = field(default_factory=dict)
    #: 4- and 5-minute penalties a team took: one can yield several PP goals.
    long_penalties: dict[TeamGame, int] = field(default_factory=dict)
    #: (season, team) -> division name, for every team-season we know.
    divisions: dict[tuple[int, int], str] = field(default_factory=dict)

    def is_partial(self, game: GameFacts) -> bool:
        """A full box score whose goal events fall short of the score: only part
        of the game was entered. Every event-based number in it is incomplete."""
        return game.is_full and any(
            self.goals.get((game.game_id, team), GoalTotals()).goals
            < game.goals_for(team) - game.shootout_credit(team)
            for team in game.teams()
        )

    def goals_after_pull(self, game: GameFacts, team: int) -> list[GoalTime]:
        """Opponent goals scored at or after ``team``'s last goalie left the net,
        in regulation. Empty: the goalie played to the end (or no times entered)."""
        stints = self.stints.get((game.game_id, team))
        if not stints:
            return []
        out = stints[-1].out
        if out >= (3, 1200):
            return []
        return [
            goal
            for goal in self.goal_times.get((game.game_id, game.opponent(team)), [])
            if goal.seconds is not None and (goal.period, goal.seconds) >= out
        ]

    def team_games(self, *, full_only: bool) -> list[tuple[GameFacts, int]]:
        return [
            (game, team)
            for game in self.games.values()
            if game.is_full or not full_only
            for team in game.teams()
        ]


def gather(session: Session) -> Facts:
    games = {
        row.game_id: GameFacts(
            game_id=row.game_id,
            season_id=row.season_id,
            home_team_id=row.home_team_id,
            visiting_team_id=row.visiting_team_id,
            home_goals=row.home_goals,
            visiting_goals=row.visiting_goals,
            shootout=row.shootout,
            box_score_kind=row.box_score_kind,
            attendance=row.attendance,
        )
        for row in session.execute(
            select(db.Game).where(db.Game.is_final, db.Game.summary_normalized)
        ).scalars()
    }
    season_of = {gid: g.season_id for gid, g in games.items()}

    team_stats = {
        (r.game_id, r.team_id): TeamStats(
            r.shots, r.goals, r.pp_goals, r.pp_opportunities, r.penalty_minutes
        )
        for r in session.scalars(select(db.GameTeamStats))
    }

    goals: dict[TeamGame, GoalTotals] = defaultdict(GoalTotals)
    g = db.GoalEvent
    for r in session.execute(
        select(
            g.game_id,
            g.team_id,
            func.count(),
            func.sum(case((g.is_empty_net, 1), else_=0)),
            func.sum(case((g.is_power_play, 1), else_=0)),
            func.sum(case((g.seconds.is_(None), 1), else_=0)),
            func.count(g.scorer_id),
        ).group_by(g.game_id, g.team_id)
    ):
        goals[(r[0], r[1])] = GoalTotals(r[2], r[3] or 0, r[4] or 0, r[5] or 0, r[6])
    for game_id, team_id, n in session.execute(
        select(g.game_id, g.team_id, func.count())
        .join(db.GoalAssist, (db.GoalAssist.game_id == g.game_id)
              & (db.GoalAssist.goal_id == g.goal_id))
        .group_by(g.game_id, g.team_id)
    ):
        goals[(game_id, team_id)].assists = n

    s = db.SkaterGameStats
    skaters = {
        (r[0], r[1]): SkaterTotals(r[2], r[3] or 0, r[4] or 0, r[5] or 0)
        for r in session.execute(
            select(
                s.game_id, s.team_id, func.count(), func.sum(s.goals), func.sum(s.assists),
                func.sum(s.penalty_minutes),
            ).group_by(s.game_id, s.team_id)
        )
    }

    stints: dict[TeamGame, list[Stint]] = defaultdict(list)
    a = db.GoalieAppearance
    for stint in session.scalars(select(a).order_by(a.game_id, a.team_id, a.sequence)):
        stints[(stint.game_id, stint.team_id)].append(
            Stint(stint.player_id, stint.shots_against, stint.goals_against, stint.saves,
                  (stint.period_out, stint.seconds_out))
        )

    p = db.PenaltyEvent
    penalty_minutes: dict[TeamGame, int] = {}
    advantage: dict[TeamGame, int] = {}
    for r in session.execute(
        select(
            p.game_id,
            p.team_id,
            func.sum(p.minutes),
            func.sum(case((p.minutes.in_(_MAN_ADVANTAGE_MINUTES), 1), else_=0)),
        ).group_by(p.game_id, p.team_id)
    ):
        penalty_minutes[(r[0], r[1])] = r[2] or 0
        advantage[(r[0], r[1])] = r[3] or 0

    ps = db.PeriodStats
    periods = {
        r[0]: (r[1] or 0, r[2] or 0, r[3] or 0, r[4] or 0)
        for r in session.execute(
            select(
                ps.game_id, func.sum(ps.home_goals), func.sum(ps.home_shots),
                func.sum(ps.visiting_goals), func.sum(ps.visiting_shots),
            ).group_by(ps.game_id)
        )
    }

    appearances: dict[tuple[int, int], set[int]] = defaultdict(set)
    for game_id, team_id, player_id in session.execute(
        select(s.game_id, s.team_id, s.player_id).union(
            select(a.game_id, a.team_id, a.player_id)
        )
    ):
        if game_id in season_of:
            appearances[(season_of[game_id], team_id)].add(player_id)

    rosters: dict[tuple[int, int], set[int]] = defaultdict(set)
    re_ = db.RosterEntry
    for season_id, team_id, player_id in session.execute(
        select(re_.season_id, re_.team_id, re_.player_id)
    ):
        rosters[(season_id, team_id)].add(player_id)

    divisions: dict[tuple[int, int], str] = {}
    md1: set[tuple[int, int]] = set()
    for ts in session.scalars(select(db.TeamSeason)):
        divisions[(ts.season_id, ts.team_id)] = ts.division_name
        if ts.is_md1:
            md1.add((ts.season_id, ts.team_id))

    skater_seasons = {
        (r[0], r[1], r[2]): SkaterTotals(r[3], r[4] or 0, r[5] or 0, r[6] or 0)
        for r in session.execute(
            select(
                db.Game.season_id, s.team_id, s.player_id, func.count(), func.sum(s.goals),
                func.sum(s.assists), func.sum(s.penalty_minutes),
            )
            .join(db.Game, db.Game.game_id == s.game_id)
            .where(db.Game.is_final, s.position != "G")
            .group_by(db.Game.season_id, s.team_id, s.player_id)
        )
    }

    goal_times: dict[TeamGame, list[GoalTime]] = defaultdict(list)
    for game_id, team_id, period, seconds, empty_net in session.execute(
        select(g.game_id, g.team_id, g.period, g.seconds, g.is_empty_net)
    ):
        goal_times[(game_id, team_id)].append(GoalTime(period, seconds, empty_net))

    goalie_ids = {(gid, tid, pid) for (gid, tid), ss in stints.items() for pid in
                  (x.player_id for x in ss)}
    skater_ids = {
        (game_id, team_id, player_id)
        for game_id, team_id, player_id in session.execute(
            select(s.game_id, s.team_id, s.player_id)
        )
    }
    goalie_assists: dict[TeamGame, int] = defaultdict(int)
    for game_id, team_id, player_id in session.execute(
        select(g.game_id, g.team_id, db.GoalAssist.player_id)
        .join(db.GoalAssist, (db.GoalAssist.game_id == g.game_id)
              & (db.GoalAssist.goal_id == g.goal_id))
    ):
        key = (game_id, team_id, player_id)
        if key in goalie_ids and key not in skater_ids:
            goalie_assists[(game_id, team_id)] += 1

    long_penalties = {
        (r[0], r[1]): r[2]
        for r in session.execute(
            select(p.game_id, p.team_id, func.count())
            .where(p.minutes.in_((4, 5)))
            .group_by(p.game_id, p.team_id)
        )
    }

    return Facts(
        games=games,
        skater_seasons=skater_seasons,
        goal_times=goal_times,
        goalie_assists=goalie_assists,
        long_penalties=long_penalties,
        divisions=divisions,
        team_stats=team_stats,
        goals=goals,
        skaters=skaters,
        stints=stints,
        penalty_minutes=penalty_minutes,
        advantage_penalties=advantage,
        periods=periods,
        appearances=appearances,
        rosters=rosters,
        md1=md1,
        upstream=list(session.scalars(select(db.UpstreamPlayerSeasonStats))),
    )
