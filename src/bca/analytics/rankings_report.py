"""The "Where we stand" page (M6): the rebuilt ranking, UC's place, what's left.

Built offline by ``bca scouting build`` and served verbatim, like the scouting
and special-teams pages. Once the ACHA publishes its own weekly ranking, its
rank leads and ours sits beside it (decided with the user); before that, only
the rebuild is shown, labelled as one.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.acha_pdf import RankCheck, compare
from bca.analytics.rankings import (
    LINE,
    RANKED_SEASONS,
    BaseRate,
    RankRow,
    base_rate,
    load_ranking_season,
    national_table,
    ranked_games,
    same_date_in,
)
from bca.analytics.scouting import UC, display_name
from bca.db import schema as db
from bca.ingest import endpoints


class _Model(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class RankingRow(RankRow):
    #: The conference, from the feed's division name ("MD1 SECHL" -> "SECHL").
    league: str
    #: The team's place in the latest official ACHA ranking, when one is published:
    #: its rank, Ranking A and B ranks, and their average.
    official_rank: int | None
    official_rank_a: int | None
    official_rank_b: int | None
    official_avg: float | None


class RemainingGame(_Model):
    game_id: int
    date: date | None
    home: bool
    opponent_id: int
    opponent: str
    opponent_rank: int | None
    opponent_official_rank: int | None


class TeamStanding(_Model):
    team_id: int
    team: str
    #: Our rebuilt rank, and the ACHA's latest (None before its first release).
    rank: int | None
    official_rank: int | None
    #: From the headline rank (official when published): +3 is 3 places above the
    #: line, -9 is 9 places below it, 0 is 24th.
    places_from_line: int | None
    remaining: list[RemainingGame]
    base_rate: BaseRate | None


class RankingsReport(_Model):
    season_id: int
    season: str
    built_at: datetime
    #: The last game the rebuild counts.
    games_through: date | None
    games: int
    median_games: float
    line: int
    rows: list[RankingRow]
    #: The date of the latest official ranking the rows' ``official_rank`` comes from.
    official_as_of: date | None
    uc: TeamStanding | None
    checks: list[RankCheck]


def _history(session: Session, season_id: int, on: date) -> dict[str, tuple[list[db.Game],
                                                                        set[int], date]]:
    """Every finished 24-team season but this one, with ``on``'s date in it."""
    out: dict[str, tuple[list[db.Game], set[int], date]] = {}
    for other in RANKED_SEASONS:
        if other in (season_id, endpoints.CURRENT_MENS_SEASON_ID):
            continue
        past = load_ranking_season(session, other)
        if past.games:
            out[past.label] = (past.games, past.md1, same_date_in(int(past.label[:4]), on))
    return out


def build_rankings_report(session: Session, season_id: int,
                          now: datetime | None = None) -> RankingsReport:
    season = load_ranking_season(session, season_id)
    names = {t: display_name(n) for t, n in season.names.items()}
    games = ranked_games(season.games, season.md1)
    national = national_table(session, season, names)
    table, official = national.rows, national.official
    checks = [compare(season, week) for week in national.weeks]

    def published_row(team_id: int) -> dict[str, int | float | None]:
        p = official.get(team_id)
        return {"official_rank": p.rank if p else None, "official_rank_a": p.rank_a if p else None,
                "official_rank_b": p.rank_b if p else None, "official_avg": p.avg if p else None}

    rows = [RankingRow(**r.model_dump(), **published_row(r.team_id),
                       league=display_name(season.divisions.get(r.team_id, "")))
            for r in table]
    by_team = {r.team_id: r for r in rows}
    through = max((g.game_date for g in games if g.game_date), default=None)

    uc: TeamStanding | None = None
    if UC in season.md1:
        row = by_team[UC]
        headline = row.official_rank or row.rank
        remaining = []
        for g in season.upcoming:
            if UC not in (g.home_team_id, g.visiting_team_id):
                continue
            opponent = g.visiting_team_id if g.home_team_id == UC else g.home_team_id
            if opponent not in by_team:  # not MD1: the ranking doesn't count it
                continue
            if g.game_date and through and g.game_date < through:
                continue  # never played, and the season has moved past it
            remaining.append(RemainingGame(
                game_id=g.game_id, date=g.game_date, home=g.home_team_id == UC,
                opponent_id=opponent, opponent=by_team[opponent].team,
                opponent_rank=by_team[opponent].rank,
                opponent_official_rank=by_team[opponent].official_rank))
        # History says how teams ranked like this finished: nothing left to play, no question.
        rate = (base_rate(_history(session, season_id, through), row.rank, through)
                if remaining and row.rank is not None and through is not None else None)
        uc = TeamStanding(team_id=UC, team=row.team, rank=row.rank,
                          official_rank=row.official_rank,
                          places_from_line=LINE - headline if headline else None,
                          remaining=remaining, base_rate=rate)

    counted = [r.games for r in rows if r.games]
    return RankingsReport(
        season_id=season_id, season=season.label, built_at=now or datetime.now(UTC),
        games_through=through, games=len(games),
        median_games=statistics.median(counted) if counted else 0.0, line=LINE, rows=rows,
        official_as_of=national.official_as_of, uc=uc, checks=checks,
    )


def store_rankings_report(sessions: sessionmaker[Session], season_id: int) -> RankingsReport:
    with sessions() as session:
        report = build_rankings_report(session, season_id)
        session.merge(db.StoredRankingsReport(season_id=season_id,
                                              payload=report.model_dump_json(),
                                              built_at=report.built_at))
        session.commit()
    return report


def rankings_schema() -> str:
    return json.dumps(RankingsReport.model_json_schema(mode="serialization"), indent=2) + "\n"
