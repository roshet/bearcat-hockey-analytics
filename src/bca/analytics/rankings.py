"""The ACHA M1 computer ranking, rebuilt from game rows (M6).

The national tournament's 24 teams are chosen and seeded by the M1
"Computerized Ranking Average" (ACHA manual 2026-27, Men's D1, Art. IV-V):
the average of two ratings published by USHSHO, ties broken by the second.

- Ranking A: each game's goal difference, capped at 7.
- Ranking B: capped at 1, so only win, loss or tie counts.
- Only games between two ACHA M1 teams count, and overtime and shootout
  results count as ties.

USHSHO describes the rating as starting every team equal and adjusting until
expected and actual goal differences agree as closely as they can: a least
squares fit of ``rating_home - rating_away = capped margin``. That fit, on our
game rows, picks the same 24 teams as the ACHA's published 2025-26 final
(see docs/data-sources.md, "Rankings (M6)").
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from itertools import groupby

import numpy as np
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from bca.db import schema as db
from bca.ingest import endpoints

#: "MD1 TBD": a schedule placeholder, never a real side.
PLACEHOLDER_TEAM = 534
#: The national tournament field.
LINE = 24
#: Seasons with a 24-team field (2023-24 on), which get a rankings page.
RANKED_SEASONS = (34, 46, 60, 73)
#: Ranking A and Ranking B's caps on a game's goal difference.
CAP_A, CAP_B = 7, 1


SEASON_LABELS = {season_id: name for name, season_id in endpoints.MENS_REGULAR_SEASONS.items()}


@dataclass
class RankingSeason:
    """What the ranking needs of one season: final scores, MD1 membership, names."""

    season_id: int
    label: str
    games: list[db.Game]
    upcoming: list[db.Game]
    md1: set[int]
    names: dict[int, str]
    #: The feed's division name per team this season, e.g. "MD1 SECHL".
    divisions: dict[int, str] = field(default_factory=dict)


def load_ranking_season(session: Session, season_id: int) -> RankingSeason:
    """Every final game counts, box score loaded or not: the ranking needs only the score."""
    games = list(session.scalars(select(db.Game).where(db.Game.season_id == season_id)))
    team_seasons = list(session.scalars(
        select(db.TeamSeason).where(db.TeamSeason.season_id == season_id)))
    names = dict(session.execute(select(db.Team.team_id, db.Team.name)).all())
    names.update({ts.team_id: ts.name for ts in team_seasons})
    return RankingSeason(
        season_id=season_id,
        label=SEASON_LABELS.get(season_id, str(season_id)),
        games=[g for g in games if g.is_final],
        upcoming=sorted((g for g in games if not g.is_final),
                        key=lambda g: (g.game_date or date.max, g.game_id)),
        md1={ts.team_id for ts in team_seasons if ts.is_md1} - {PLACEHOLDER_TEAM},
        names=names,
        divisions={ts.team_id: ts.division_name for ts in team_seasons},
    )


class _Model(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class RankRow(_Model):
    team_id: int
    team: str
    #: MD1-vs-MD1 games counted.
    games: int
    #: None for a team with no MD1-vs-MD1 game yet.
    rank: int | None
    rank_a: int | None
    rank_b: int | None
    avg: float | None
    rating_a: float
    rating_b: float
    #: Mean Ranking A rating of the opponents played.
    schedule: float | None
    #: The record the ACHA's way: overtime and shootout results are ties.
    w: int
    l: int  # noqa: E741  (hockey's own abbreviation)
    t: int
    #: How many of the ties were overtime or shootout wins and losses.
    otw: int
    otl: int


def ranked_games(games: Iterable[db.Game], md1: set[int],
                 as_of: date | None = None) -> list[db.Game]:
    """The games the ranking counts: both sides MD1, played on or before ``as_of``.

    Forfeits ("Home Forfeit", "Visiting Forfeit") are left out: without them the
    rebuild matches the ACHA's published ranking better on every PDF that has
    one, and the manual handles forfeits as a separate penalty, not as a score.
    """
    teams = md1 - {PLACEHOLDER_TEAM}
    return [g for g in games
            if g.home_team_id in teams and g.visiting_team_id in teams
            and "forfeit" not in (g.status or "").lower()
            and (as_of is None or (g.game_date is not None and g.game_date <= as_of))]


def _margin(game: db.Game, cap: int) -> int:
    if game.overtime or game.shootout:
        return 0
    return max(-cap, min(cap, game.home_goals - game.visiting_goals))


def rate(games: list[db.Game], teams: list[int], cap: int) -> dict[int, float]:
    """Least-squares ratings, summing to zero; a team with no game rates 0."""
    if not games:
        return dict.fromkeys(teams, 0.0)
    index = {t: i for i, t in enumerate(teams)}
    rows = np.zeros((len(games) + 1, len(teams)))
    target = np.zeros(len(games) + 1)
    for i, g in enumerate(games):
        rows[i, index[g.home_team_id]] = 1.0
        rows[i, index[g.visiting_team_id]] = -1.0
        target[i] = _margin(g, cap)
    rows[-1, :] = 1.0  # the ratings sum to zero
    solution = np.linalg.lstsq(rows, target, rcond=None)[0]
    return {t: float(solution[index[t]]) for t in teams}


def _ranks(ratings: dict[int, float], ranked: set[int]) -> dict[int, int]:
    # Rounded so float noise between equal ratings cannot reorder them.
    order = sorted(ranked, key=lambda t: (-round(ratings[t], 9), t))
    return {t: i + 1 for i, t in enumerate(order)}


def average_order(rank_a: dict[int, int], rank_b: dict[int, int]) -> dict[int, int]:
    """The final order: mean of the two ranks, ties to the better Ranking B."""
    order = sorted(rank_a, key=lambda t: ((rank_a[t] + rank_b[t]) / 2, rank_b[t], t))
    return {t: i + 1 for i, t in enumerate(order)}


def rank_table(games: list[db.Game], md1: set[int], names: dict[int, str]) -> list[RankRow]:
    """Every MD1 team, ranked; teams with no counted game last, unranked."""
    games = ranked_games(games, md1)
    teams = sorted(md1 - {PLACEHOLDER_TEAM})
    played: Counter[int] = Counter()
    opponents: dict[int, list[int]] = defaultdict(list)
    record: dict[int, Counter[str]] = defaultdict(Counter)
    for g in games:
        home, away = g.home_team_id, g.visiting_team_id
        played.update([home, away])
        opponents[home].append(away)
        opponents[away].append(home)
        for side, gf, ga in ((home, g.home_goals, g.visiting_goals),
                             (away, g.visiting_goals, g.home_goals)):
            if g.overtime or g.shootout:
                record[side]["t"] += 1
                record[side]["otw" if gf > ga else "otl"] += 1
            else:
                record[side]["w" if gf > ga else "l" if gf < ga else "t"] += 1

    ratings_a, ratings_b = rate(games, teams, CAP_A), rate(games, teams, CAP_B)
    ranked = {t for t in teams if played[t]}
    rank_a, rank_b = _ranks(ratings_a, ranked), _ranks(ratings_b, ranked)
    final = average_order(rank_a, rank_b)

    rows = [
        RankRow(
            team_id=t, team=names.get(t, str(t)), games=played[t], rank=final.get(t),
            rank_a=rank_a.get(t), rank_b=rank_b.get(t),
            avg=(rank_a[t] + rank_b[t]) / 2 if t in ranked else None,
            # + 0.0 turns a rounded -0.0 into 0.0.
            rating_a=round(ratings_a[t], 3) + 0.0, rating_b=round(ratings_b[t], 3) + 0.0,
            schedule=(round(sum(ratings_a[o] for o in opponents[t]) / len(opponents[t]), 3)
                      if opponents[t] else None),
            w=record[t]["w"], l=record[t]["l"], t=record[t]["t"],
            otw=record[t]["otw"], otl=record[t]["otl"],
        )
        for t in teams
    ]
    return sorted(rows, key=lambda r: (r.rank is None, r.rank or 0, r.team))


@dataclass
class NationalTable:
    """The rebuilt ranking plus every official ACHA week: what both pages rank from."""

    rows: list[RankRow]
    #: The latest published week, by team.
    official: dict[int, db.PublishedRanking]
    official_as_of: date | None
    #: Every published week, oldest first.
    weeks: list[list[db.PublishedRanking]]


def national_table(session: Session, season: RankingSeason,
                   names: dict[int, str]) -> NationalTable:
    rows = rank_table(ranked_games(season.games, season.md1), season.md1, names)
    published = list(session.scalars(
        select(db.PublishedRanking).where(db.PublishedRanking.season_id == season.season_id)
        .order_by(db.PublishedRanking.as_of, db.PublishedRanking.rank)))
    weeks = [list(week) for _, week in groupby(published, key=lambda p: p.as_of)]
    return NationalTable(
        rows=rows,
        official={p.team_id: p for p in weeks[-1]} if weeks else {},
        official_as_of=weeks[-1][0].as_of if weeks else None,
        weeks=weeks,
    )


# --- how teams ranked like this finished -------------------------------------------


class BaseRate(_Model):
    """Of the teams ranked ``low``-``high`` on the same date in past seasons, how many
    finished in the top 24. History, not a prediction (the user's choice for M6)."""

    seasons: list[str]
    #: The calendar date compared, e.g. "Oct 24".
    on: str
    low: int
    high: int
    teams: int
    made_line: int


#: Fewer teams than this to compare against and the base rate is not shown.
BASE_RATE_MINIMUM = 10


def same_date_in(season_start_year: int, on: date) -> date:
    """``on``'s month and day in the season starting ``season_start_year`` (Aug-Jul)."""
    year = season_start_year if on.month >= 8 else season_start_year + 1
    try:
        return on.replace(year=year)
    except ValueError:  # Feb 29 in a year without one
        return date(year, 2, 28)


def base_rate(history: dict[str, tuple[list[db.Game], set[int], date]], rank: int, on: date,
              band: int = 5) -> BaseRate | None:
    """Count teams ranked within ``band`` of ``rank`` on the same date in each past season,
    and how many of them finished ranked inside the line."""
    low, high = max(1, rank - band), rank + band
    teams = made = 0
    for games, md1, then in history.values():
        names: dict[int, str] = {}
        early = {r.team_id: r.rank for r in rank_table(ranked_games(games, md1, then), md1, names)}
        final = {r.team_id: r.rank for r in rank_table(games, md1, names)}
        band_teams = [t for t, r in early.items() if r is not None and low <= r <= high]
        teams += len(band_teams)
        made += sum((final[t] or LINE + 1) <= LINE for t in band_teams)
    if teams < BASE_RATE_MINIMUM:
        return None
    return BaseRate(seasons=list(history), on=f"{on:%b} {on.day}", low=low, high=high,
                    teams=teams, made_line=made)
