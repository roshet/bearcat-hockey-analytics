"""The read-only API over stored scouting reports."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.scouting import ScoutingReport, store_reports
from bca.analytics.special_teams import run_special_teams
from bca.analytics.team_detail import TeamDetailReport, store_detail_reports
from bca.api.app import create_app
from bca.audit.run import run_audit
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENTUCKY = 346


@pytest.fixture
def client(tmp_path, sessions: sessionmaker[Session]) -> TestClient:
    archive: RawArchive = build_archive(tmp_path / "cache")
    for game_id in (33157, 33171):
        archive_payload(archive, endpoints.game_summary(game_id),
                        payload(f"game_summary_{game_id}"))
    Normalizer(sessions, archive).run()
    run_audit(sessions)
    run_special_teams(sessions)
    store_reports(sessions, 73, [KENTUCKY, UC])
    store_detail_reports(sessions, 73, [KENTUCKY, UC])
    return TestClient(create_app(sessions))


def test_special_teams_serves_the_stored_page(client):
    response = client.get(f"/api/special-teams/{UC}")
    assert response.status_code == 200
    page = TeamDetailReport.model_validate(response.json())
    assert page.players_as_numbers
    assert client.get("/api/special-teams/99999").status_code == 404


def test_the_web_apps_detail_schema_is_up_to_date():
    from bca.analytics.team_detail import detail_schema
    from bca.cli import DETAIL_SCHEMA

    assert DETAIL_SCHEMA.read_text(encoding="utf-8") == detail_schema()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_teams_lists_built_reports(client):
    teams = client.get("/api/teams").json()
    assert {t["team_id"] for t in teams} == {KENTUCKY, UC}
    assert all(t["team"] and t["division"] for t in teams)


def test_scouting_serves_the_stored_report(client):
    response = client.get(f"/api/scouting/{KENTUCKY}")
    assert response.status_code == 200
    report = ScoutingReport.model_validate(response.json())  # the shape the web app relies on
    assert report.team == "University of Kentucky"
    assert report.current.record.gp == 2


def test_unknown_team_or_season_is_404(client):
    assert client.get("/api/scouting/99999").status_code == 404
    assert client.get(f"/api/scouting/{KENTUCKY}?season=60").status_code == 404


def test_the_api_is_read_only(client):
    assert client.post(f"/api/scouting/{KENTUCKY}").status_code == 405


def test_the_web_apps_schema_is_up_to_date():
    """web/src/types/report.ts is generated from this file; regenerate with
    `bca scouting schema` then `cd web && npm run gen:types`."""
    from bca.analytics.scouting import report_schema
    from bca.cli import REPORT_SCHEMA

    assert REPORT_SCHEMA.read_text(encoding="utf-8") == report_schema()


def test_rebuilding_replaces_rather_than_duplicates(client, sessions):
    store_reports(sessions, 73, [KENTUCKY])
    assert len(client.get("/api/teams").json()) == 2
