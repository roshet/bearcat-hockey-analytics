"""The static export (M7): stored pages written where the web app reads them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.rankings_report import store_rankings_report
from bca.analytics.scouting import store_reports
from bca.analytics.special_teams import run_special_teams
from bca.analytics.team_detail import store_detail_reports
from bca.api.app import create_app
from bca.audit.run import run_audit
from bca.db import schema as db
from bca.export import export_static
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENTUCKY = 346
NOW = datetime(2026, 10, 24, 11, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path, sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    archive: RawArchive = build_archive(tmp_path / "cache")
    for game_id in (33157, 33171):
        archive_payload(archive, endpoints.game_summary(game_id),
                        payload(f"game_summary_{game_id}"))
    Normalizer(sessions, archive).run()
    run_audit(sessions)
    run_special_teams(sessions)
    store_reports(sessions, 73, [KENTUCKY, UC])
    store_detail_reports(sessions, 73, [KENTUCKY, UC])
    store_rankings_report(sessions, 73)
    return sessions


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_export_writes_every_stored_page_where_the_app_looks(seeded, tmp_path):
    out = tmp_path / "data"
    summary = export_static(seeded, out, now=NOW)
    index = read(out / "index.json")
    assert isinstance(index, dict)
    assert index["current_season"] == 73 and index["seasons"] == [73]
    assert index["built_at"] == NOW.isoformat()
    assert index["data_through"] == summary.data_through.isoformat()  # type: ignore[union-attr]
    teams = read(out / "73" / "teams.json")
    assert {t["team_id"] for t in teams} == {KENTUCKY, UC}  # type: ignore[union-attr]
    with seeded() as session:
        stored = session.get(db.StoredScoutingReport, (KENTUCKY, 73))
        assert stored is not None
        assert read(out / "73" / "scouting" / f"{KENTUCKY}.json") == json.loads(stored.payload)
    assert (out / "73" / "special-teams" / f"{UC}.json").exists()
    assert (out / "73" / "rankings.json").exists()
    assert summary.files == 1 + 1 + 2 + 2 + 1


def test_teams_json_matches_the_api(seeded, tmp_path):
    out = tmp_path / "data"
    export_static(seeded, out, now=NOW)
    api = TestClient(create_app(seeded)).get("/api/teams").json()
    assert read(out / "73" / "teams.json") == api


def test_a_failed_export_leaves_the_previous_one_in_place(seeded, tmp_path, monkeypatch):
    out = tmp_path / "data"
    export_static(seeded, out, now=NOW)
    before = (out / "index.json").read_bytes()

    import bca.export as export

    real = export._write
    calls = {"n": 0}

    def failing(path: Path, data: object) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(export, "_write", failing)
    with pytest.raises(OSError):
        export_static(seeded, out, now=datetime(2026, 10, 25, tzinfo=UTC))
    assert (out / "index.json").read_bytes() == before
    assert not [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name or ".old-" in p.name]


def test_the_digest_ignores_built_at(seeded, tmp_path):
    first = export_static(seeded, tmp_path / "a", now=NOW)
    second = export_static(seeded, tmp_path / "b", now=datetime(2026, 10, 25, tzinfo=UTC))
    assert first.digest == second.digest


def test_a_briefly_locked_folder_is_retried(seeded, tmp_path, monkeypatch):
    # Windows refuses a rename while another process (a dev server's file watcher)
    # holds the folder for a moment.
    import bca.export as export

    monkeypatch.setattr(export, "RETRY_DELAY", 0)
    real = Path.replace
    refusals = {"left": 2}

    def flaky(self: Path, target: Path) -> Path:
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError("[WinError 5] Access is denied")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    export_static(seeded, tmp_path / "data", now=NOW)
    assert (tmp_path / "data" / "index.json").exists()


def test_rebuilding_unchanged_pages_keeps_the_digest(seeded, tmp_path):
    # Every stored page carries its own build time; a nightly rebuild with no new
    # games must still count as "unchanged", or the site redeploys every day.
    first = export_static(seeded, tmp_path / "a", now=NOW)
    store_reports(seeded, 73, [KENTUCKY, UC])
    store_detail_reports(seeded, 73, [KENTUCKY, UC])
    store_rankings_report(seeded, 73)
    second = export_static(seeded, tmp_path / "b", now=NOW)
    assert first.digest == second.digest


def test_folders_left_by_an_interrupted_export_are_swept(seeded, tmp_path):
    # A hard kill, or a folder Windows wouldn't let go of, leaves these behind; Vite would
    # copy them into the build and they'd grow with every deploy.
    out = tmp_path / "data"
    for name in ("data.tmp-123", "data.old-456"):
        (tmp_path / name / "73").mkdir(parents=True)
    export_static(seeded, out, now=NOW)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("data")) == ["data"]
