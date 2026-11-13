"""The national rank on the scouting report: the same ranks Where we stand shows."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.rankings import PLACEHOLDER_TEAM, load_ranking_season, national_table
from bca.analytics.scouting import build_reports, national_rank
from bca.db import schema as db
from tests.unit.test_rankings_report import UC, publish, seed

NOW = datetime(2026, 10, 24, tzinfo=UTC)
NO_GAMES = 1099


def _table(session: Session):
    season = load_ranking_season(session, 73)
    return national_table(session, season, season.names)


def test_before_any_official_list_every_rank_is_rebuilt(sessions: sessionmaker[Session]):
    with sessions() as session:
        seed(session)
        r = national_rank(_table(session), 1001)
    assert (r.rank, r.source, r.of, r.games) == (1, "rebuilt", 40, 39)


def test_a_team_missing_from_the_official_list_falls_back_to_rebuilt(
        sessions: sessionmaker[Session]):
    with sessions() as session:
        seed(session)
        publish(session, {UC: 30, 1001: 1}, as_of=date(2026, 11, 4))
        t = _table(session)
        uc, other = national_rank(t, UC), national_rank(t, 1005)
    assert (uc.source, uc.rank, uc.of) == ("acha", 30, 2)
    assert (other.source, other.rank, other.of) == ("rebuilt", 5, 40)


def test_a_team_without_an_md1_game_is_unranked(sessions: sessionmaker[Session]):
    with sessions() as session:
        seed(session)
        session.add(db.TeamSeason(team_id=NO_GAMES, season_id=73, name="MD1 Idle",
                                  division_name="MD1", is_md1=True))
        session.commit()
        r = national_rank(_table(session), NO_GAMES)
    assert (r.rank, r.source, r.games) == (None, None, 0)


def test_uc_own_report_has_no_uc_comparison(sessions: sessionmaker[Session]):
    with sessions() as session:
        seed(session)
        own, other = build_reports(session, 73, [UC, 1001], now=NOW)
    assert own.national.uc is None
    assert own.national.team.team_id == UC
    assert other.national.uc is not None and other.national.uc.team_id == UC
    assert other.national.line == 24


def test_the_placeholder_team_is_unranked_not_an_error(sessions: sessionmaker[Session]):
    """"MD1 TBD" counts as MD1 for the scouting report but never in the ranking."""
    with sessions() as session:
        seed(session)
        session.add(db.TeamSeason(team_id=PLACEHOLDER_TEAM, season_id=73, name="MD1 TBD",
                                  division_name="MD1", is_md1=True))
        session.commit()
        r = national_rank(_table(session), PLACEHOLDER_TEAM)
    assert (r.rank, r.source, r.games) == (None, None, 0)
