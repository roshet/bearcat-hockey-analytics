"""The opponent scouting report: an MD1 team's profile, assembled from game rows.

Every number comes from the normalized game rows and the M3 derived tables --
never from the league's own summaries (convention 1). Every section says how
many games it rests on, and which games it had to leave out (convention 6):

- goals by period: every completed game (period goals are entered even in
  score-only box scores);
- shots, penalties, scorers, goalies: full box scores only;
- special teams: games the penalty-state simulator could run (M3).

The report is an *opponent profile*: UC appears only in the head-to-head
section, never as a comparison column.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.infractions import infraction_name
from bca.analytics.rankings import LINE, NationalTable, load_ranking_season, national_table
from bca.db import schema as db
from bca.ingest import endpoints

T = TypeVar("T")
M = TypeVar("M", bound=db.Base)

UC = 265
#: Teams with fewer games than this are left out of league ranks.
RANK_MINIMUM_GAMES = 5
SEASON_LABELS = {season_id: name for name, season_id in endpoints.MENS_REGULAR_SEASONS.items()}

# --- infractions -------------------------------------------------------------------

#: How many named infractions the report lists before "everything else".
TOP_INFRACTIONS = 8
#: How many recent goalie starts the report lists (four back-to-back weekends).
RECENT_STARTS = 8

# --- the report model -------------------------------------------------------------


class _Model(BaseModel):
    #: Fields with defaults are always present in the JSON; say so in the schema,
    #: so the web app's generated types are not needlessly optional.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class Record(_Model):
    gp: int = 0
    w: int = 0
    l: int = 0  # noqa: E741  (hockey's own abbreviation)
    otl: int = 0
    sol: int = 0
    gf: int = 0
    ga: int = 0


class GameResult(_Model):
    game_id: int
    date: date | None
    home: bool
    opponent: str
    gf: int
    ga: int
    #: W / L, with OT or SO appended when it went past regulation.
    result: str


class Quality(_Model):
    games: int
    full_box_scores: int
    score_only: int
    partial: int
    pp_entered: int
    special_teams_games: int
    notes: list[str]


class Form(_Model):
    last5: list[GameResult]
    home: Record
    road: Record
    one_goal: Record
    overtime: Record
    #: Opponents' combined win% in their other games; None when unknown.
    opponents_win_pct: float | None
    opponents_games: int


class PeriodRow(_Model):
    period: str
    gf: int
    ga: int
    sf: int
    sa: int


class ByPeriod(_Model):
    games: int
    shot_games: int
    rows: list[PeriodRow]
    scored_first: Record
    allowed_first: Record
    first_goal_games: int
    leading_after_two: Record
    tied_after_two: Record
    trailing_after_two: Record


class Shots(_Model):
    games: int
    sf_per_game: float | None
    sa_per_game: float | None
    shot_share: float | None


class SpecialTeams(_Model):
    games: int
    pp_opportunities: int
    pp_goals: int
    pp_pct: float | None
    pk_opportunities: int
    pp_goals_against: int
    pk_pct: float | None
    pp_minutes_per_game: float | None
    pk_minutes_per_game: float | None
    pp_goals_per_60: float | None
    sh_goals: int
    sh_goals_against: int
    #: Games where the scorekeeper's entered opportunities differ from ours.
    entered_disagrees: int


class InfractionCount(_Model):
    infraction: str
    count: int
    per_game: float
    #: The same infraction's rate for every team in this season's full box scores.
    league_per_game: float


class PenaltyTaker(_Model):
    player_id: int
    name: str
    penalties: int
    pim: int


class Discipline(_Model):
    games: int
    pim_per_game: float | None
    infractions_per_game: float | None
    minors_per_game: float | None
    #: The most frequent named infractions, most first.
    infractions: list[InfractionCount]
    #: Penalties not among ``infractions``.
    other_infractions: int
    top_takers: list[PenaltyTaker]


class Scorer(_Model):
    player_id: int
    name: str
    gp: int
    g: int
    a: int
    p: int
    pp_goals: int
    #: Points rank among every MD1 skater this season (None for a prior season).
    md1_rank: int | None
    #: Another MD1 skater has the same points: shown as "T-12th".
    md1_tied: bool
    #: The points ``md1_rank`` ranks: every MD1 team the player dressed for, so more than
    #: ``p`` for a player who changed teams (None for a prior season).
    md1_points: int | None


class Scoring(_Model):
    games: int
    goals: int
    top: list[Scorer]
    top3_goal_share: float | None
    #: Share of their goals scored on a (derived) power play.
    pp_goal_share: float | None
    #: MD1 skaters with a game this season, the pool ``md1_rank`` ranks among (0 for a
    #: prior season).
    md1_skaters: int


class Goalie(_Model):
    player_id: int
    name: str
    gp: int
    starts: int
    minutes: int
    shots_against: int
    goals_against: int
    sv_pct: float | None
    gaa: float | None
    #: Starts in which another goalie came in (not an empty net, which is no stint).
    replaced: int


class GoalieStart(_Model):
    game_id: int
    date: date | None
    #: "Fri", "Sat"... -- most MD1 weekends are back-to-back games.
    weekday: str
    home: bool
    goalie: str


class Goaltending(_Model):
    games: int
    goalies: list[Goalie]
    #: Who started, newest game first: shows a rotation no season table can.
    recent_starts: list[GoalieStart]


class LeagueRank(_Model):
    metric: str
    value: float | None
    rank: int | None
    of: int
    higher_is_better: bool


class NationalRank(_Model):
    team_id: int
    team: str
    #: The ACHA's rank when this team is in its latest list, else our rebuilt one;
    #: None for a team with no MD1-vs-MD1 game yet.
    rank: int | None
    source: Literal["acha", "rebuilt"] | None
    #: Teams in that source's list: the official week's size, or the teams we rank.
    of: int
    #: MD1-vs-MD1 games the rebuilt ranking counts for this team.
    games: int


class National(_Model):
    """Where the team sits nationally, beside UC: the ranks Where we stand shows."""

    team: NationalRank
    #: None on UC's own report, or when UC isn't MD1.
    uc: NationalRank | None
    official_as_of: date | None
    #: The national tournament takes the top ``line`` teams.
    line: int


class SeasonBlock(_Model):
    season_id: int
    season: str
    record: Record
    quality: Quality
    form: Form
    by_period: ByPeriod
    shots: Shots
    special_teams: SpecialTeams
    discipline: Discipline
    scoring: Scoring
    goaltending: Goaltending
    ranks: list[LeagueRank]


class HeadToHead(_Model):
    season: str
    game_id: int
    date: date | None
    home: bool
    gf: int
    ga: int
    result: str
    venue: str


class UpcomingGame(_Model):
    game_id: int
    date: date | None
    home: bool
    opponent_id: int
    opponent: str


class ScoutingReport(_Model):
    team_id: int
    team: str
    division: str
    built_at: datetime
    current: SeasonBlock
    prior: SeasonBlock | None
    head_to_head: list[HeadToHead]
    next_games: list[UpcomingGame]
    national: National


# --- season data, loaded once ------------------------------------------------------


@dataclass
class _Season:
    season_id: int
    games: dict[int, db.Game]
    md1: set[int]
    team_names: dict[int, str]
    divisions: dict[int, str]
    periods: dict[int, list[db.PeriodStats]]
    goals: dict[int, list[db.GoalEvent]]
    penalties: dict[int, list[db.PenaltyEvent]]
    skaters: dict[int, list[db.SkaterGameStats]]
    goalies: dict[int, list[db.GoalieAppearance]]
    team_stats: dict[tuple[int, int], db.GameTeamStats]
    special: dict[tuple[int, int], db.TeamGameSpecialTeams]
    goal_strength: dict[tuple[int, int], db.GoalStrength]
    outcomes: dict[int, db.PenaltyOutcome]
    quality: dict[tuple[int, int], db.GameQuality]
    players: dict[int, str]
    upcoming: list[db.Game] = field(default_factory=list)
    #: Infraction -> per-team-game rate across every full box score this season.
    league_infraction_rates: dict[str, float] = field(default_factory=dict)
    #: (game, goal) -> penalties being served at that power-play goal (M3).
    serving: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    #: (team, player) -> jersey number this season: box-score lines, else roster.
    jerseys: dict[tuple[int, int], int] = field(default_factory=dict)

    def team_games(self, team_id: int) -> list[db.Game]:
        return sorted(
            (g for g in self.games.values() if team_id in (g.home_team_id, g.visiting_team_id)),
            key=lambda g: (g.game_date or date.min, g.game_id),
        )


def _grouped(rows: Iterable[T], key: Callable[[T], int]) -> dict[int, list[T]]:
    out: dict[int, list[T]] = defaultdict(list)
    for row in rows:
        out[key(row)].append(row)
    return out


def load_season(session: Session, season_id: int) -> _Season:
    all_games = list(session.scalars(select(db.Game).where(db.Game.season_id == season_id)))
    games = {g.game_id: g for g in all_games if g.is_final and g.summary_normalized}
    ids = set(games)

    def where_game(model: type[M]) -> Select[M]:
        return select(model).join(
            db.Game, db.Game.game_id == model.game_id  # type: ignore[attr-defined]
        ).where(db.Game.season_id == season_id)

    team_seasons = list(session.scalars(
        select(db.TeamSeason).where(db.TeamSeason.season_id == season_id)
    ))
    names = dict(session.execute(select(db.Team.team_id, db.Team.name)).all())
    names.update({ts.team_id: ts.name for ts in team_seasons})
    goal_strength = {
        (gs.game_id, gs.goal_id): gs for gs in session.scalars(where_game(db.GoalStrength))
    }
    penalties = _grouped(session.scalars(where_game(db.PenaltyEvent)), lambda r: r.game_id)
    full = [gid for gid, g in games.items() if g.box_score_kind == "full"]
    league = Counter(
        infraction_name(p.minutes, p.description) for gid in full for p in penalties.get(gid, [])
    )
    team_games = 2 * len(full)
    serving: dict[tuple[int, int], list[int]] = defaultdict(list)
    for row in session.scalars(where_game(db.GoalStrengthPenalty)):
        serving[(row.game_id, row.goal_id)].append(row.penalty_id)
    skaters = _grouped(session.scalars(where_game(db.SkaterGameStats)), lambda r: r.game_id)
    jerseys: dict[tuple[int, int], int] = {
        (r.team_id, r.player_id): r.jersey_number
        for r in session.scalars(select(db.RosterEntry).where(
            db.RosterEntry.season_id == season_id))
        if r.jersey_number is not None
    }
    seen: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
    for lines in skaters.values():
        for line in lines:
            if line.jersey_number is not None:
                seen[(line.team_id, line.player_id)][line.jersey_number] += 1
    jerseys.update({key: numbers.most_common(1)[0][0] for key, numbers in seen.items()})
    return _Season(
        serving=dict(serving),
        jerseys=jerseys,
        season_id=season_id,
        games=games,
        md1={ts.team_id for ts in team_seasons if ts.is_md1},
        team_names=names,
        divisions={ts.team_id: ts.division_name for ts in team_seasons},
        periods=_grouped(session.scalars(where_game(db.PeriodStats)), lambda r: r.game_id),
        goals=_grouped(session.scalars(where_game(db.GoalEvent)), lambda r: r.game_id),
        penalties=penalties,
        league_infraction_rates={
            name: round(n / team_games, 2) for name, n in league.items()
        } if team_games else {},
        skaters=skaters,
        goalies=_grouped(session.scalars(where_game(db.GoalieAppearance)), lambda r: r.game_id),
        team_stats={(r.game_id, r.team_id): r
                    for r in session.scalars(where_game(db.GameTeamStats))},
        special={(r.game_id, r.team_id): r
                 for r in session.scalars(where_game(db.TeamGameSpecialTeams))},
        goal_strength=goal_strength,
        outcomes={r.penalty_id: r for r in session.scalars(where_game(db.PenaltyOutcome))},
        quality={(r.game_id, r.team_id): r for r in session.scalars(where_game(db.GameQuality))
                 if r.game_id in ids},
        players={
            pid: f"{first} {last}".strip()
            for pid, first, last in session.execute(
                select(db.Player.player_id, db.Player.first_name, db.Player.last_name)
            )
        },
        upcoming=sorted((g for g in all_games if not g.is_final),
                        key=lambda g: (g.game_date or date.max, g.game_id)),
    )


# --- small helpers -------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _per_game(total: float, games: int) -> float | None:
    return round(total / games, 2) if games else None


def display_name(name: str) -> str:
    """'MD1 University of Kentucky' -> 'University of Kentucky'."""
    for prefix in ("MD1 ", "MD2 ", "M1 ", "M2 "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


@dataclass(frozen=True)
class _Side:
    """One game from one team's point of view."""

    game: db.Game
    team_id: int

    @property
    def home(self) -> bool:
        return self.game.home_team_id == self.team_id

    @property
    def opponent_id(self) -> int:
        return self.game.visiting_team_id if self.home else self.game.home_team_id

    @property
    def gf(self) -> int:
        return self.game.home_goals if self.home else self.game.visiting_goals

    @property
    def ga(self) -> int:
        return self.game.visiting_goals if self.home else self.game.home_goals

    @property
    def won(self) -> bool:
        return self.gf > self.ga

    @property
    def result(self) -> str:
        suffix = " SO" if self.game.shootout else " OT" if self.game.overtime else ""
        return ("W" if self.won else "L") + suffix


def _record(sides: list[_Side]) -> Record:
    r = Record()
    for s in sides:
        r.gp += 1
        r.gf += s.gf
        r.ga += s.ga
        if s.won:
            r.w += 1
        elif s.game.shootout:
            r.sol += 1
        elif s.game.overtime:
            r.otl += 1
        else:
            r.l += 1
    return r


# --- sections --------------------------------------------------------------------------


def _quality(season: _Season, team_id: int, sides: list[_Side]) -> Quality:
    rows = [season.quality.get((s.game.game_id, team_id)) for s in sides]
    full = sum(s.game.box_score_kind == "full" for s in sides)
    score_only = sum(s.game.box_score_kind == "score_only" for s in sides)
    partial = sum(1 for q, s in zip(rows, sides, strict=True)
                  if q is not None and s.game.box_score_kind == "full" and not q.goal_events)
    pp_entered = sum(1 for q in rows if q is not None and q.pp_opportunities)
    simulated = sum((s.game.game_id, team_id) in season.special for s in sides)
    notes = []
    if score_only:
        notes.append(f"{score_only} of {len(sides)} games have a score-only box score: "
                     "their shots, penalties, scorers and goalies were never entered.")
    if partial:
        notes.append(f"{partial} box scores were only partly entered.")
    if simulated < len(sides):
        notes.append(f"Special teams rest on {simulated} of {len(sides)} games.")
    if len(sides) < 10:
        notes.append(f"Only {len(sides)} games: treat every rate as a rough indication.")
    return Quality(games=len(sides), full_box_scores=full, score_only=score_only,
                   partial=partial, pp_entered=pp_entered, special_teams_games=simulated,
                   notes=notes)


def _form(season: _Season, team_id: int, sides: list[_Side]) -> Form:
    last5 = [
        GameResult(game_id=s.game.game_id, date=s.game.game_date, home=s.home,
                   opponent=display_name(season.team_names.get(s.opponent_id, "?")),
                   gf=s.gf, ga=s.ga, result=s.result)
        for s in sides[-5:]
    ][::-1]
    # Opponents' win% in their *other* games: what these results were earned against.
    wins = games = 0
    for s in sides:
        for other in season.team_games(s.opponent_id):
            if team_id in (other.home_team_id, other.visiting_team_id):
                continue
            games += 1
            wins += _Side(other, s.opponent_id).won
    return Form(
        last5=last5,
        home=_record([s for s in sides if s.home]),
        road=_record([s for s in sides if not s.home]),
        one_goal=_record([s for s in sides if abs(s.gf - s.ga) == 1 and not s.game.shootout]),
        overtime=_record([s for s in sides if s.game.overtime or s.game.shootout]),
        opponents_win_pct=_ratio(wins, games),
        opponents_games=games,
    )


def _by_period(season: _Season, sides: list[_Side]) -> ByPeriod:
    totals: dict[str, list[int]] = {label: [0, 0, 0, 0] for label in ("1", "2", "3", "OT")}
    shot_games = 0
    after_two: dict[str, list[_Side]] = {"lead": [], "tied": [], "trail": []}
    first_for: list[_Side] = []
    first_against: list[_Side] = []
    for s in sides:
        full = s.game.box_score_kind == "full"
        shot_games += full
        two = [0, 0]
        for p in season.periods.get(s.game.game_id, []):
            label = str(p.period) if p.period <= 3 else "OT"
            gf, ga = (p.home_goals, p.visiting_goals) if s.home else (p.visiting_goals,
                                                                        p.home_goals)
            sf, sa = (p.home_shots, p.visiting_shots) if s.home else (p.visiting_shots,
                                                                        p.home_shots)
            row = totals[label]
            row[0] += gf
            row[1] += ga
            if full:
                row[2] += sf
                row[3] += sa
            if p.period <= 2:
                two[0] += gf
                two[1] += ga
        key = "lead" if two[0] > two[1] else "tied" if two[0] == two[1] else "trail"
        after_two[key].append(s)
        timed = [g for g in season.goals.get(s.game.game_id, []) if g.seconds is not None]
        if timed and len(timed) == len(season.goals.get(s.game.game_id, [])):
            first = min(timed, key=lambda g: (g.period, g.seconds or 0, g.goal_id))
            (first_for if first.team_id == s.team_id else first_against).append(s)
    return ByPeriod(
        games=len(sides),
        shot_games=shot_games,
        rows=[PeriodRow(period=label, gf=v[0], ga=v[1], sf=v[2], sa=v[3])
              for label, v in totals.items() if label != "OT" or v[0] or v[1]],
        scored_first=_record(first_for),
        allowed_first=_record(first_against),
        first_goal_games=len(first_for) + len(first_against),
        leading_after_two=_record(after_two["lead"]),
        tied_after_two=_record(after_two["tied"]),
        trailing_after_two=_record(after_two["trail"]),
    )


def _shots(season: _Season, sides: list[_Side]) -> Shots:
    full = [s for s in sides if s.game.box_score_kind == "full"]
    sf = sum(season.team_stats[(s.game.game_id, s.team_id)].shots for s in full
             if (s.game.game_id, s.team_id) in season.team_stats)
    sa = sum(season.team_stats[(s.game.game_id, s.opponent_id)].shots for s in full
             if (s.game.game_id, s.opponent_id) in season.team_stats)
    return Shots(games=len(full), sf_per_game=_per_game(sf, len(full)),
                 sa_per_game=_per_game(sa, len(full)), shot_share=_ratio(sf, sf + sa))


def _special_teams(season: _Season, sides: list[_Side]) -> SpecialTeams:
    mine = [season.special[(s.game.game_id, s.team_id)] for s in sides
            if (s.game.game_id, s.team_id) in season.special]
    theirs = [season.special[(s.game.game_id, s.opponent_id)] for s in sides
              if (s.game.game_id, s.team_id) in season.special]
    n = len(mine)
    opps = sum(m.pp_opportunities for m in mine)
    ppg = sum(m.pp_goals for m in mine)
    pk_opps = sum(t.pp_opportunities for t in theirs)
    ppga = sum(m.pp_goals_against for m in mine)
    pp_seconds = sum(m.pp_seconds for m in mine)
    pk_seconds = sum(m.pk_seconds for m in mine)
    return SpecialTeams(
        games=n,
        pp_opportunities=opps,
        pp_goals=ppg,
        pp_pct=_ratio(ppg, opps),
        pk_opportunities=pk_opps,
        pp_goals_against=ppga,
        pk_pct=round(1 - ppga / pk_opps, 4) if pk_opps else None,
        pp_minutes_per_game=_per_game(pp_seconds / 60, n),
        pk_minutes_per_game=_per_game(pk_seconds / 60, n),
        pp_goals_per_60=round(ppg * 3600 / pp_seconds, 2) if pp_seconds else None,
        sh_goals=sum(m.sh_goals for m in mine),
        sh_goals_against=sum(m.sh_goals_against for m in mine),
        entered_disagrees=sum(
            1 for m in mine
            if m.entered_pp_opportunities is not None
            and m.entered_pp_opportunities != m.pp_opportunities
        ),
    )


def _discipline(season: _Season, sides: list[_Side]) -> Discipline:
    full = [s for s in sides if s.game.box_score_kind == "full"]
    penalties = [p for s in full for p in season.penalties.get(s.game.game_id, [])
                 if p.team_id == s.team_id]
    n = len(full)
    named = Counter(infraction_name(p.minutes, p.description) for p in penalties)
    top = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_INFRACTIONS]
    takers: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for p in penalties:
        if p.taken_by_id is not None:
            takers[p.taken_by_id][0] += 1
            takers[p.taken_by_id][1] += p.minutes
    total = len(penalties)
    return Discipline(
        games=n,
        pim_per_game=_per_game(sum(p.minutes for p in penalties), n),
        infractions_per_game=_per_game(total, n),
        minors_per_game=_per_game(sum(p.minutes in (2, 12) for p in penalties), n),
        infractions=[
            InfractionCount(infraction=name, count=count, per_game=round(count / n, 2),
                            league_per_game=season.league_infraction_rates.get(name, 0.0))
            for name, count in top
        ] if n else [],
        other_infractions=total - sum(count for _, count in top),
        top_takers=[
            PenaltyTaker(player_id=pid, name=season.players.get(pid, str(pid)),
                         penalties=v[0], pim=v[1])
            for pid, v in sorted(takers.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:5]
        ],
    )


def league_points(season: _Season) -> dict[int, int]:
    """Points per MD1 skater over every MD1 team's full box scores, the same lines the
    team tables count; a player on two MD1 teams is counted once, combined."""
    points: dict[int, int] = {}
    for team_id in season.md1:
        for game in season.team_games(team_id):
            if game.box_score_kind != "full":
                continue
            for line in season.skaters.get(game.game_id, []):
                if line.team_id == team_id and line.position != "G":
                    points[line.player_id] = (points.get(line.player_id, 0)
                                              + line.goals + line.assists)
    return points


def points_ranks(points: dict[int, int]) -> dict[int, tuple[int, bool]]:
    """Player -> (1 + players with more points, whether another player has as many)."""
    counts = Counter(points.values())
    ordered = sorted(counts, reverse=True)
    above, first = 0, {}
    for value in ordered:
        first[value] = above + 1
        above += counts[value]
    return {pid: (first[p], counts[p] > 1) for pid, p in points.items()}


def _scoring(season: _Season, sides: list[_Side],
             league: dict[int, tuple[int, bool, int]] | None = None) -> Scoring:
    full = [s for s in sides if s.game.box_score_kind == "full"]
    lines: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])  # gp, g, a
    pp_goals: Counter[int] = Counter()
    goals = derived_pp = simulated_goals = 0
    for s in full:
        for line in season.skaters.get(s.game.game_id, []):
            if line.team_id == s.team_id and line.position != "G":
                row = lines[line.player_id]
                row[0] += 1
                row[1] += line.goals
                row[2] += line.assists
        for g in season.goals.get(s.game.game_id, []):
            if g.team_id != s.team_id:
                continue
            goals += 1
            strength = season.goal_strength.get((g.game_id, g.goal_id))
            if strength is not None:
                simulated_goals += 1
                if strength.situation == "pp":
                    derived_pp += 1
                    if g.scorer_id is not None:
                        pp_goals[g.scorer_id] += 1
    ranked = sorted(lines.items(), key=lambda kv: (-(kv[1][1] + kv[1][2]), -kv[1][1], kv[0]))
    top = [
        Scorer(player_id=pid, name=season.players.get(pid, str(pid)), gp=v[0], g=v[1], a=v[2],
               p=v[1] + v[2], pp_goals=pp_goals[pid],
               md1_rank=league[pid][0] if league and pid in league else None,
               md1_tied=league[pid][1] if league and pid in league else False,
               md1_points=league[pid][2] if league and pid in league else None)
        for pid, v in ranked[:8]
    ]
    by_goals = sorted((v[1] for v in lines.values()), reverse=True)
    return Scoring(
        games=len(full),
        goals=goals,
        top=top,
        top3_goal_share=_ratio(sum(by_goals[:3]), sum(by_goals)),
        pp_goal_share=_ratio(derived_pp, simulated_goals),
        md1_skaters=len(league) if league else 0,
    )


def _goaltending(season: _Season, sides: list[_Side]) -> Goaltending:
    full = [s for s in sides if s.game.box_score_kind == "full"]
    rows: dict[int, Counter[str]] = defaultdict(Counter)
    games: dict[int, set[int]] = defaultdict(set)
    starts: list[GoalieStart] = []
    for s in full:
        stints = sorted((a for a in season.goalies.get(s.game.game_id, [])
                         if a.team_id == s.team_id), key=lambda a: a.sequence)
        for a in stints:
            r = rows[a.player_id]
            games[a.player_id].add(s.game.game_id)
            r["minutes"] += a.toi_seconds or 0
            r["sa"] += a.shots_against
            r["ga"] += a.goals_against
        if stints:
            starter = stints[0].player_id
            rows[starter]["starts"] += 1
            if any(a.player_id != starter for a in stints[1:]):
                rows[starter]["replaced"] += 1
            starts.append(GoalieStart(
                game_id=s.game.game_id, date=s.game.game_date,
                weekday=s.game.game_date.strftime("%a") if s.game.game_date else "",
                home=s.home, goalie=season.players.get(starter, str(starter)),
            ))
    goalies = [
        Goalie(
            player_id=pid, name=season.players.get(pid, str(pid)), gp=len(games[pid]),
            starts=r["starts"], minutes=r["minutes"] // 60, shots_against=r["sa"],
            goals_against=r["ga"], sv_pct=_ratio(r["sa"] - r["ga"], r["sa"]),
            gaa=round(r["ga"] * 3600 / r["minutes"], 2) if r["minutes"] else None,
            replaced=r["replaced"],
        )
        for pid, r in sorted(rows.items(), key=lambda kv: (-kv[1]["starts"], -kv[1]["minutes"]))
    ]
    # sides are in date order, so the last starts are the most recent.
    return Goaltending(games=len(full), goalies=goalies,
                       recent_starts=starts[::-1][:RECENT_STARTS])


# --- league ranks --------------------------------------------------------------------

_RANKED: tuple[tuple[str, bool], ...] = (
    ("goals for per game", True),
    ("goals against per game", False),
    ("shot share", True),
    ("power play %", True),
    ("penalty kill %", True),
    ("penalty minutes per game", False),
)


def _rank_values(block: SeasonBlock) -> dict[str, float | None]:
    gp = block.record.gp
    return {
        "goals for per game": _per_game(block.record.gf, gp),
        "goals against per game": _per_game(block.record.ga, gp),
        "shot share": block.shots.shot_share,
        "power play %": block.special_teams.pp_pct,
        "penalty kill %": block.special_teams.pk_pct,
        "penalty minutes per game": block.discipline.pim_per_game,
    }


def _ranks(blocks: dict[int, SeasonBlock]) -> dict[int, list[LeagueRank]]:
    return rank_teams(
        {t: _rank_values(b) for t, b in blocks.items()},
        {t for t, b in blocks.items() if b.record.gp >= RANK_MINIMUM_GAMES},
        _RANKED,
    )


def rank_teams(
    values: dict[int, dict[str, float | None]],
    eligible: set[int],
    metrics: tuple[tuple[str, bool], ...],
) -> dict[int, list[LeagueRank]]:
    """Rank each team on each (metric, higher_is_better) among ``eligible`` teams.

    Teams outside ``eligible`` (too few games) get their value but no rank.
    """
    out: dict[int, list[LeagueRank]] = {}
    for team_id, own in values.items():
        ranks = []
        for metric, higher in metrics:
            pool = [x for t in eligible if (x := values[t][metric]) is not None]
            value = own[metric]
            rank = None
            if team_id in eligible and value is not None:
                rank = 1 + sum((x > value) if higher else (x < value) for x in pool)
            ranks.append(LeagueRank(metric=metric, value=value, rank=rank, of=len(pool),
                                    higher_is_better=higher))
        out[team_id] = ranks
    return out


# --- national rank -------------------------------------------------------------------


def national_rank(table: NationalTable, team_id: int) -> NationalRank:
    """The same per-team fallback as Where we stand: official if listed, else rebuilt."""
    row = next((r for r in table.rows if r.team_id == team_id), None)
    ranked = sum(r.rank is not None for r in table.rows)
    if row is None:  # the placeholder "MD1 TBD": MD1 in the feed, never ranked
        return NationalRank(team_id=team_id, team=str(team_id), rank=None, source=None,
                            of=ranked, games=0)
    official = table.official.get(team_id)
    if official is not None:
        rank, source, of = official.rank, "acha", len(table.official)
    elif row.rank is not None:
        rank, source, of = row.rank, "rebuilt", ranked
    else:
        rank, source, of = None, None, ranked
    return NationalRank(team_id=team_id, team=row.team, rank=rank, source=source, of=of,
                        games=row.games)


def _national(table: NationalTable, team_id: int) -> National:
    has_uc = team_id != UC and any(r.team_id == UC for r in table.rows)
    return National(team=national_rank(table, team_id),
                    uc=national_rank(table, UC) if has_uc else None,
                    official_as_of=table.official_as_of, line=LINE)


# --- assembly --------------------------------------------------------------------------


def season_block(season: _Season, team_id: int,
                 league: dict[int, tuple[int, bool, int]] | None = None) -> SeasonBlock:
    sides = [_Side(g, team_id) for g in season.team_games(team_id)]
    return SeasonBlock(
        season_id=season.season_id,
        season=SEASON_LABELS.get(season.season_id, str(season.season_id)),
        record=_record(sides),
        quality=_quality(season, team_id, sides),
        form=_form(season, team_id, sides),
        by_period=_by_period(season, sides),
        shots=_shots(season, sides),
        special_teams=_special_teams(season, sides),
        discipline=_discipline(season, sides),
        scoring=_scoring(season, sides, league),
        goaltending=_goaltending(season, sides),
        ranks=[],
    )


def build_reports(
    session: Session, season_id: int, team_ids: list[int] | None = None,
    now: datetime | None = None,
) -> list[ScoutingReport]:
    """Reports for the season's MD1 teams (or ``team_ids``), with league ranks."""
    now = now or datetime.now(UTC)
    current = load_season(session, season_id)
    previous_id = _previous_season(season_id)
    prior = load_season(session, previous_id) if previous_id is not None else None

    ranking = load_ranking_season(session, season_id)
    national = national_table(session, ranking,
                              {t: display_name(n) for t, n in ranking.names.items()})

    points = league_points(current)
    league = {pid: (*rank, points[pid]) for pid, rank in points_ranks(points).items()}
    blocks = {t: season_block(current, t, league) for t in current.md1}
    for team_id, ranks in _ranks(blocks).items():
        blocks[team_id].ranks = ranks

    wanted = sorted(team_ids or current.md1)
    reports = []
    for team_id in wanted:
        if team_id not in blocks:
            raise ValueError(f"team {team_id} is not an MD1 team in season {season_id}")
        prior_block = None
        if prior is not None and prior.team_games(team_id):
            prior_block = season_block(prior, team_id)
        reports.append(ScoutingReport(
            team_id=team_id,
            team=display_name(current.team_names.get(team_id, str(team_id))),
            division=current.divisions.get(team_id, ""),
            built_at=now,
            current=blocks[team_id],
            prior=prior_block,
            head_to_head=[] if team_id == UC else _head_to_head(session, team_id),
            next_games=[
                UpcomingGame(game_id=g.game_id, date=g.game_date,
                             home=g.home_team_id == team_id, opponent_id=opponent_id,
                             opponent=display_name(current.team_names.get(opponent_id, "?")))
                for g in current.upcoming
                for opponent_id in [g.visiting_team_id if g.home_team_id == team_id
                                    else g.home_team_id]
                if team_id in (g.home_team_id, g.visiting_team_id)
                and (g.game_date is None or g.game_date >= now.date())
            ][:4],
            national=_national(national, team_id),
        ))
    return reports


def report_schema() -> str:
    """The JSON schema the web app's TypeScript types are generated from."""
    import json

    return json.dumps(ScoutingReport.model_json_schema(mode="serialization"), indent=2) + "\n"


def store_reports(
    sessions: sessionmaker[Session], season_id: int, team_ids: list[int] | None = None
) -> list[ScoutingReport]:
    """Build and (re)place the stored reports. The API serves exactly these."""
    with sessions.begin() as session:
        reports = build_reports(session, season_id, team_ids)
        for report in reports:
            session.merge(db.StoredScoutingReport(
                team_id=report.team_id, season_id=season_id, team=report.team,
                division=report.division, payload=report.model_dump_json(),
                built_at=report.built_at,
            ))
    return reports


def _previous_season(season_id: int) -> int | None:
    ordered = sorted(SEASON_LABELS)
    index = ordered.index(season_id) if season_id in ordered else -1
    return ordered[index - 1] if index > 0 else None


def _head_to_head(session: Session, team_id: int) -> list[HeadToHead]:
    g = db.Game
    games = session.scalars(
        select(g).where(
            g.is_final,
            ((g.home_team_id == team_id) & (g.visiting_team_id == UC))
            | ((g.home_team_id == UC) & (g.visiting_team_id == team_id)),
        ).order_by(g.game_date.desc(), g.game_id.desc())
    ).all()
    return [
        HeadToHead(
            season=SEASON_LABELS.get(game.season_id, str(game.season_id)),
            game_id=game.game_id, date=game.game_date, home=side.home, gf=side.gf,
            ga=side.ga, result=side.result, venue=game.venue,
        )
        for game in games
        for side in (_Side(game, team_id),)
    ]
