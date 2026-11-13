"""The stored "Where we stand" page (M6): built from a small seeded season."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.rankings import load_ranking_season, national_table
from bca.analytics.rankings_report import (
    RankingsReport,
    build_rankings_report,
    rankings_schema,
    store_rankings_report,
)
from bca.api.app import create_app
from bca.db import schema as db
from tests.unit.test_rankings import game

UC = 265
NOW = datetime(2026, 10, 24, tzinfo=UTC)
#: 40 teams in a strict ladder; UC sits 27th.
LADDER = [*range(1001, 1027), UC, *range(1027, 1040)]
NON_MD1 = 2001


def seed(session: Session, season_id: int = 73, teams: list[int] = LADDER,
         played: date = date(2026, 10, 10)) -> None:
    session.add_all(db.TeamSeason(team_id=t, season_id=season_id, name=f"MD1 Team {t}",
                                  division_name="MD1", is_md1=True) for t in teams)
    session.add(db.TeamSeason(team_id=NON_MD1, season_id=season_id, name="MD2 Elsewhere",
                              division_name="MD2", is_md1=False))
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            g = game(a, b, 3, 0, on=played)
            g.season_id = season_id
            session.add(g)
    for opponent, on in ((1003, date(2026, 11, 6)), (NON_MD1, date(2026, 11, 13)),
                         (1030, date(2026, 11, 20))):
        g = game(UC, opponent, 0, 0, on=on)
        g.season_id, g.is_final = season_id, False
        session.add(g)
    session.commit()


def publish(session: Session, ranks: dict[int, int], as_of: date = date(2026, 11, 4)) -> None:
    session.add_all(db.PublishedRanking(season_id=73, as_of=as_of, team_id=t, rank=r, rank_a=r,
                                        rank_b=r, avg=float(r), league="X", source_file="f.pdf")
                    for t, r in ranks.items())
    session.commit()


@pytest.fixture
def seeded(sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    with sessions() as session:
        seed(session)
    return sessions


def test_uc_block_is_absent_when_uc_is_not_md1(sessions):
    with sessions() as session:
        seed(session, teams=[t for t in LADDER if t != UC])
        report = build_rankings_report(session, 73, now=NOW)
    assert report.uc is None and len(report.rows) == 39


def test_remaining_games_are_md1_only_and_carry_the_opponents_rank(seeded):
    with seeded() as session:
        report = build_rankings_report(session, 73, now=NOW)
    assert report.uc is not None
    assert [(g.opponent_id, g.opponent_rank) for g in report.uc.remaining] == [(1003, 3),
                                                                               (1030, 31)]
    assert report.uc.remaining[0].opponent == "Team 1003" and report.uc.remaining[0].home


def test_before_any_pdf_official_fields_are_null_and_checks_empty(seeded):
    with seeded() as session:
        report = build_rankings_report(session, 73, now=NOW)
    assert report.official_as_of is None and report.checks == []
    assert all(r.official_rank is None for r in report.rows)
    assert report.uc is not None
    assert (report.uc.rank, report.uc.official_rank, report.uc.places_from_line) == (27, None, -3)
    assert report.games == 780 and report.median_games == 39
    assert report.games_through == date(2026, 10, 10) and report.line == 24


def test_places_from_line_uses_the_official_rank_when_published(seeded):
    with seeded() as session:
        ranks = {t: i + 1 for i, t in enumerate(LADDER)}
        ranks[UC], ranks[1029] = 30, 27  # the ACHA has UC 30th; we rebuild 27th
        publish(session, ranks)
        report = build_rankings_report(session, 73, now=NOW)
    assert report.official_as_of == date(2026, 11, 4) and len(report.checks) == 1
    assert report.uc is not None
    assert (report.uc.rank, report.uc.official_rank, report.uc.places_from_line) == (27, 30, -6)
    uc_row = next(r for r in report.rows if r.team_id == UC)
    assert uc_row.official_rank == 30
    # The row carries the ACHA's own A, B and average too, so a table led by the
    # official rank shows the official columns beside it.
    assert (uc_row.official_rank_a, uc_row.official_rank_b, uc_row.official_avg) == (30, 30, 30.0)
    assert report.uc.remaining[1].opponent_official_rank == 31


def test_the_rankings_endpoint_serves_the_stored_payload(seeded):
    store_rankings_report(seeded, 73)
    client = TestClient(create_app(seeded))
    response = client.get("/api/rankings")
    assert response.status_code == 200
    report = RankingsReport.model_validate(response.json())
    assert report.season == "2026-27" and report.uc is not None


def test_a_season_without_a_rankings_page_is_404(seeded):
    store_rankings_report(seeded, 73)
    client = TestClient(create_app(seeded))
    response = client.get("/api/rankings?season=60")
    assert response.status_code == 404
    assert response.json()["detail"] == "no rankings page for season 60"


def test_rebuilding_replaces_the_stored_page(seeded):
    store_rankings_report(seeded, 73)
    store_rankings_report(seeded, 73)
    with seeded() as session:
        assert len(session.query(db.StoredRankingsReport).all()) == 1


def test_the_web_apps_rankings_schema_is_up_to_date():
    from bca.cli import RANKINGS_SCHEMA

    assert RANKINGS_SCHEMA.read_text(encoding="utf-8") == rankings_schema()


def test_an_unplayed_game_dated_before_the_last_counted_game_is_not_remaining(seeded):
    with seeded() as session:
        stale = game(UC, 1005, 0, 0, on=date(2026, 10, 1))  # never played; the season went on
        stale.season_id, stale.is_final = 73, False
        session.add(stale)
        session.commit()
        report = build_rankings_report(session, 73, now=NOW)
    assert report.uc is not None
    assert stale.game_id not in {g.game_id for g in report.uc.remaining}


def test_a_finished_season_has_no_base_rate(sessions):
    with sessions() as session:
        seed(session, season_id=60, played=date(2025, 10, 10))
        # History that would otherwise give a base rate (11 teams ranked 22nd-32nd).
        seed(session, season_id=34, played=date(2023, 10, 10))
        for g in session.query(db.Game).filter(db.Game.is_final.is_(False)).all():
            session.delete(g)
        session.commit()
        report = build_rankings_report(session, 60, now=NOW)
    assert report.uc is not None and report.uc.remaining == [] and report.uc.base_rate is None


def test_rows_carry_the_teams_conference(sessions):
    with sessions() as session:
        seed(session)
        session.get(db.TeamSeason, (UC, 73)).division_name = "MD1 SECHL"
        session.commit()
        report = build_rankings_report(session, 73, now=NOW)
    assert next(r for r in report.rows if r.team_id == UC).league == "SECHL"


def test_national_table_carries_the_latest_official_week(seeded):
    with seeded() as session:
        publish(session, {1001: 1, UC: 30}, as_of=date(2026, 11, 4))
        publish(session, {1001: 2, UC: 29}, as_of=date(2026, 11, 11))
        season = load_ranking_season(session, 73)
        t = national_table(session, season, season.names)
    assert t.official_as_of == date(2026, 11, 11)
    assert t.official[UC].rank == 29
    assert len(t.weeks) == 2
    assert 1002 not in t.official
    assert [r.team_id for r in t.rows][:2] == [1001, 1002]
