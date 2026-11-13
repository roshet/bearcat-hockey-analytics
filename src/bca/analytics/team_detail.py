"""Special teams & discipline: the game-by-game detail behind a team's numbers.

The companion to the scouting report (M4), for any MD1 team. It adds what a
two-page handout has no room for, and what nobody publishes:

- a game log of power play and penalty kill, with the *time* spent on each;
- every power-play goal against, with the penalty that was being served;
- every short-handed goal;
- every penalised player.

What it deliberately leaves out, after checking (M5 plan): a PP%/PK% *trend*
line (0-2 PP goals a game is luck at this sample) and penalties by game
minute (indistinguishable from chance, see M4). Rates carry a likely range.

UC's own page shows players by jersey number, never by name, so a printout
never reads as blame on named teammates. Opponents' pages name players.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.scouting import (
    RANK_MINIMUM_GAMES,
    SEASON_LABELS,
    TOP_INFRACTIONS,
    UC,
    InfractionCount,
    LeagueRank,
    _Model,
    _per_game,
    _previous_season,
    _ratio,
    _Season,
    _Side,
    display_name,
    infraction_name,
    load_season,
    rank_teams,
)
from bca.db import schema as db

#: Teams whose players appear by jersey number only.
NUMBERS_ONLY = frozenset({UC})
#: z for an 80% interval: "likely range" -- wide enough to be honest, narrow
#: enough to be useful.
_Z80 = 1.2816


def likely_range(successes: int, trials: int) -> tuple[float, float] | None:
    """80% Wilson score interval for a rate such as PP% (successes / trials)."""
    if trials == 0:
        return None
    p = successes / trials
    z2 = _Z80 * _Z80
    centre = (p + z2 / (2 * trials)) / (1 + z2 / trials)
    half = _Z80 * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / (1 + z2 / trials)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def display_player(season: _Season, team_id: int, player_id: int | None) -> str:
    """A player as this report shows them: '#12' on UC's page, a name elsewhere."""
    if player_id is None:
        return "bench"
    if team_id in NUMBERS_ONLY:
        number = season.jerseys.get((team_id, player_id))
        if number is None:
            return "#?"
        # Jersey numbers are not unique (two UC goalies have shared #1): tell
        # players who share one apart without naming them.
        sharing = sorted(p for (t, p), n in season.jerseys.items() if t == team_id and n == number)
        if len(sharing) > 1:
            return f"#{number}{'abcdefgh'[sharing.index(player_id) % 8]}"
        return f"#{number}"
    return season.players.get(player_id, str(player_id))


# --- the model ---------------------------------------------------------------------


class SpecialTeamsSummary(_Model):
    games: int
    pp_goals: int
    pp_opportunities: int
    pp_pct: float | None
    pp_range: tuple[float, float] | None
    pk_opportunities: int
    pp_goals_against: int
    pk_pct: float | None
    pk_range: tuple[float, float] | None
    pp_minutes_per_game: float | None
    #: Minutes spent short-handed: the real cost of discipline (not PIM, which
    #: counts misconducts that never cost a man).
    sh_minutes_per_game: float | None
    sh_goals_for: int
    sh_goals_against: int
    #: The league box scores' own PP-opportunity total for the same games: a tally
    #: of hand-ticked flags, 0 where never entered. A PP% worked out from the
    #: league site uses it, so the page says how far it is from ours.
    league_pp_opportunities: int = 0
    #: Games whose box score never entered PP opportunities.
    games_not_entered: int = 0
    #: Games where it was entered but differs from the count rebuilt here.
    games_counted_differently: int = 0


class GameLogRow(_Model):
    game_id: int
    date: date | None
    home: bool
    opponent: str
    gf: int
    ga: int
    result: str
    simulated: bool
    pp_goals: int
    pp_opportunities: int
    pp_minutes: float
    pp_goals_against: int
    pk_opportunities: int
    pk_minutes: float
    minors: int
    #: Why a number may be missing or disputed; None when the game is clean.
    flag: str | None


class ServedPenalty(_Model):
    infraction: str
    player: str


class PowerPlayGoalAgainst(_Model):
    game_id: int
    date: date | None
    opponent: str
    period: str
    clock: str
    #: "5v4" from the scoring side's point of view.
    strength: str
    serving: list[ServedPenalty]


class ShortHandedGoal(_Model):
    game_id: int
    date: date | None
    opponent: str
    period: str
    clock: str
    #: True when this team scored it (while short-handed).
    scored_by_team: bool


class PlayerDiscipline(_Model):
    player: str
    penalties: int
    minors: int
    pim: int
    #: Minutes his penalties had the team a skater down (at 5-on-3, both
    #: penalties count). Not the goals scored meanwhile: the player is in the box, so
    #: those measure the penalty kill, and per player they don't beat chance.
    sh_minutes_caused: float


class DetailSeason(_Model):
    season_id: int
    season: str
    games: int
    summary: SpecialTeamsSummary
    game_log: list[GameLogRow]
    pp_goals_against: list[PowerPlayGoalAgainst]
    sh_goals: list[ShortHandedGoal]
    #: Over full box scores only.
    minors_per_game: float | None
    infractions: list[InfractionCount]
    other_infractions: int
    players: list[PlayerDiscipline]
    ranks: list[LeagueRank]


class TeamDetailReport(_Model):
    team_id: int
    team: str
    division: str
    built_at: datetime
    #: Players are shown by jersey number only (UC's own page).
    players_as_numbers: bool
    current: DetailSeason
    prior: DetailSeason | None


# --- assembly ---------------------------------------------------------------------


def _period(number: int) -> str:
    return "OT" if number > 3 else str(number)


def _clock(seconds: int | None) -> str:
    return "?" if seconds is None else f"{seconds // 60}:{seconds % 60:02d}"


def _flag(season: _Season, side: _Side) -> str | None:
    game = side.game
    if (game.game_id, side.team_id) not in season.special:
        if game.box_score_kind == "score_only":
            return "score-only box score: no special teams"
        return "box score only partly entered: no special teams"
    # Where the league's own PP count differs, the banner says so once for the
    # season (SpecialTeamsSummary.league_pp_opportunities), not game by game.
    return None


def _summary(season: _Season, sides: list[_Side]) -> SpecialTeamsSummary:
    mine = [season.special[(s.game.game_id, s.team_id)] for s in sides
            if (s.game.game_id, s.team_id) in season.special]
    theirs = [season.special[(s.game.game_id, s.opponent_id)] for s in sides
              if (s.game.game_id, s.team_id) in season.special]
    n = len(mine)
    ppg, ppo = sum(m.pp_goals for m in mine), sum(m.pp_opportunities for m in mine)
    ppga, pko = sum(m.pp_goals_against for m in mine), sum(t.pp_opportunities for t in theirs)
    return SpecialTeamsSummary(
        games=n, pp_goals=ppg, pp_opportunities=ppo, pp_pct=_ratio(ppg, ppo),
        pp_range=likely_range(ppg, ppo), pk_opportunities=pko, pp_goals_against=ppga,
        pk_pct=round(1 - ppga / pko, 4) if pko else None,
        pk_range=(lambda r: (round(1 - r[1], 4), round(1 - r[0], 4)) if r else None)(
            likely_range(ppga, pko)),
        pp_minutes_per_game=_per_game(sum(m.pp_seconds for m in mine) / 60, n),
        sh_minutes_per_game=_per_game(sum(m.pk_seconds for m in mine) / 60, n),
        sh_goals_for=sum(m.sh_goals for m in mine),
        sh_goals_against=sum(m.sh_goals_against for m in mine),
        league_pp_opportunities=sum(m.entered_pp_opportunities or 0 for m in mine),
        games_not_entered=sum(m.entered_pp_opportunities is None for m in mine),
        games_counted_differently=sum(
            m.entered_pp_opportunities is not None
            and m.entered_pp_opportunities != m.pp_opportunities for m in mine
        ),
    )


def _game_log(season: _Season, sides: list[_Side]) -> list[GameLogRow]:
    rows = []
    for s in sides:
        key = (s.game.game_id, s.team_id)
        mine = season.special.get(key)
        theirs = season.special.get((s.game.game_id, s.opponent_id))
        minors = sum(1 for p in season.penalties.get(s.game.game_id, [])
                     if p.team_id == s.team_id and p.minutes in (2, 12))
        rows.append(GameLogRow(
            game_id=s.game.game_id, date=s.game.game_date, home=s.home,
            opponent=display_name(season.team_names.get(s.opponent_id, "?")),
            gf=s.gf, ga=s.ga, result=s.result, simulated=mine is not None,
            pp_goals=mine.pp_goals if mine else 0,
            pp_opportunities=mine.pp_opportunities if mine else 0,
            pp_minutes=round(mine.pp_seconds / 60, 1) if mine else 0.0,
            pp_goals_against=mine.pp_goals_against if mine else 0,
            pk_opportunities=theirs.pp_opportunities if theirs else 0,
            pk_minutes=round(mine.pk_seconds / 60, 1) if mine else 0.0,
            minors=minors if s.game.box_score_kind == "full" else 0,
            flag=_flag(season, s),
        ))
    return rows


def _goal_lists(
    season: _Season, sides: list[_Side]
) -> tuple[list[PowerPlayGoalAgainst], list[ShortHandedGoal]]:
    """PP goals against (with the penalty being served) and SH goals."""
    penalties = {p.penalty_id: p for lines in season.penalties.values() for p in lines}
    pp_against: list[PowerPlayGoalAgainst] = []
    short: list[ShortHandedGoal] = []
    for s in sides:
        opponent = display_name(season.team_names.get(s.opponent_id, "?"))
        for g in sorted(season.goals.get(s.game.game_id, []),
                        key=lambda g: (g.period, g.seconds or 0, g.goal_id)):
            strength = season.goal_strength.get((g.game_id, g.goal_id))
            if strength is None:
                continue
            if strength.situation == "sh":
                short.append(ShortHandedGoal(
                    game_id=g.game_id, date=s.game.game_date, opponent=opponent,
                    period=_period(g.period), clock=_clock(g.seconds),
                    scored_by_team=g.team_id == s.team_id,
                ))
            elif strength.situation == "pp" and g.team_id != s.team_id:
                served = [penalties[pid] for pid in season.serving.get((g.game_id, g.goal_id), [])
                          if pid in penalties]
                pp_against.append(PowerPlayGoalAgainst(
                    game_id=g.game_id, date=s.game.game_date, opponent=opponent,
                    period=_period(g.period), clock=_clock(g.seconds),
                    strength=f"{strength.skaters_for}v{strength.skaters_against}",
                    serving=[ServedPenalty(
                        infraction=infraction_name(p.minutes, p.description),
                        player=display_player(season, s.team_id, p.taken_by_id),
                    ) for p in served],
                ))
    return pp_against, short


def _short_seconds(
    penalties: list[db.PenaltyEvent], outcomes: dict[int, db.PenaltyOutcome]
) -> Counter[int]:
    """Seconds each player's penalties had his team short-handed.

    The simulator credits every half of a double minor with the whole chain's
    time (the scorekeepers flag each half), so one player's minors from one
    instant count once, at their largest credit; majors each count.
    """
    chains: dict[tuple[int, int, int, int], int] = {}
    total: Counter[int] = Counter()
    for p in penalties:
        outcome = outcomes.get(p.penalty_id)
        if p.taken_by_id is None or outcome is None:
            continue
        if p.minutes in (2, 12):
            key = (p.taken_by_id, p.game_id, p.period, p.seconds or 0)
            chains[key] = max(chains.get(key, 0), outcome.advantage_seconds)
        else:
            total[p.taken_by_id] += outcome.advantage_seconds
    for (player, *_), seconds in chains.items():
        total[player] += seconds
    return total


def _discipline(
    season: _Season, sides: list[_Side]
) -> tuple[list[InfractionCount], int, list[PlayerDiscipline], float | None]:
    full = [s for s in sides if s.game.box_score_kind == "full"]
    penalties = [p for s in full for p in season.penalties.get(s.game.game_id, [])
                 if p.team_id == s.team_id]
    n = len(full)
    named = Counter(infraction_name(p.minutes, p.description) for p in penalties)
    top = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_INFRACTIONS]
    per_player: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])  # penalties, minors, pim
    for p in penalties:
        if p.taken_by_id is None:
            continue
        row = per_player[p.taken_by_id]
        row[0] += 1
        row[1] += p.minutes in (2, 12)
        row[2] += p.minutes
    short_seconds = _short_seconds(penalties, season.outcomes)
    team_id = sides[0].team_id if sides else 0
    players = [
        PlayerDiscipline(player=display_player(season, team_id, pid), penalties=v[0],
                         minors=v[1], pim=v[2],
                         sh_minutes_caused=round(short_seconds[pid] / 60, 1))
        for pid, v in sorted(per_player.items(), key=lambda kv: (-kv[1][0], -kv[1][2], kv[0]))
    ]
    infractions = [
        InfractionCount(infraction=name, count=count, per_game=round(count / n, 2),
                        league_per_game=season.league_infraction_rates.get(name, 0.0))
        for name, count in top
    ] if n else []
    minors = _per_game(sum(p.minutes in (2, 12) for p in penalties), n)
    return infractions, len(penalties) - sum(c for _, c in top), players, minors


_RANKED: tuple[tuple[str, bool], ...] = (
    ("power play %", True),
    ("penalty kill %", True),
    ("short-handed minutes per game", False),
    ("minors per game", False),
)


def _rank_values(block: DetailSeason) -> dict[str, float | None]:
    return {
        "power play %": block.summary.pp_pct,
        "penalty kill %": block.summary.pk_pct,
        "short-handed minutes per game": block.summary.sh_minutes_per_game,
        "minors per game": block.minors_per_game,
    }


def detail_season(season: _Season, team_id: int) -> DetailSeason:
    sides = [_Side(g, team_id) for g in season.team_games(team_id)]
    pp_against, short = _goal_lists(season, sides)
    infractions, other, players, minors = _discipline(season, sides)
    return DetailSeason(
        season_id=season.season_id,
        season=SEASON_LABELS.get(season.season_id, str(season.season_id)),
        games=len(sides),
        summary=_summary(season, sides),
        game_log=_game_log(season, sides),
        pp_goals_against=pp_against,
        sh_goals=short,
        minors_per_game=minors,
        infractions=infractions,
        other_infractions=other,
        players=players,
        ranks=[],
    )


def build_detail_reports(
    session: Session, season_id: int, team_ids: list[int] | None = None,
    now: datetime | None = None,
) -> list[TeamDetailReport]:
    now = now or datetime.now(UTC)
    current = load_season(session, season_id)
    previous_id = _previous_season(season_id)
    prior = load_season(session, previous_id) if previous_id is not None else None

    blocks = {t: detail_season(current, t) for t in current.md1}
    ranks = rank_teams(
        {t: _rank_values(b) for t, b in blocks.items()},
        {t for t, b in blocks.items() if b.summary.games >= RANK_MINIMUM_GAMES},
        _RANKED,
    )
    for team_id, team_ranks in ranks.items():
        blocks[team_id].ranks = team_ranks

    reports = []
    for team_id in sorted(team_ids or current.md1):
        if team_id not in blocks:
            raise ValueError(f"team {team_id} is not an MD1 team in season {season_id}")
        reports.append(TeamDetailReport(
            team_id=team_id,
            team=display_name(current.team_names.get(team_id, str(team_id))),
            division=current.divisions.get(team_id, ""),
            built_at=now,
            players_as_numbers=team_id in NUMBERS_ONLY,
            current=blocks[team_id],
            prior=detail_season(prior, team_id)
            if prior is not None and prior.team_games(team_id) else None,
        ))
    return reports


def store_detail_reports(
    sessions: sessionmaker[Session], season_id: int, team_ids: list[int] | None = None
) -> list[TeamDetailReport]:
    with sessions.begin() as session:
        reports = build_detail_reports(session, season_id, team_ids)
        for report in reports:
            session.merge(db.StoredTeamDetailReport(
                team_id=report.team_id, season_id=season_id, team=report.team,
                division=report.division, payload=report.model_dump_json(),
                built_at=report.built_at,
            ))
    return reports


def detail_schema() -> str:
    import json

    return json.dumps(TeamDetailReport.model_json_schema(mode="serialization"), indent=2) + "\n"
