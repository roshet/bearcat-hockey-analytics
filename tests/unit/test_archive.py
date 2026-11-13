"""Tests for the raw-response archive."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bca.ingest.archive import RawArchive


@pytest.fixture
def archive(tmp_path):
    return RawArchive(tmp_path / "cache")


def put(archive: RawArchive, key: str, raw_text: str, when: datetime, view: str = "seasons"):
    archive.write(
        key,
        identity=f"modulekit:{view}",
        feed="modulekit",
        params={"view": view},
        fetched_at=when,
        payload={"raw": raw_text},
        raw_text=raw_text,
    )


T1 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_round_trip(archive):
    put(archive, "modulekit.seasons.abc", "one", T1)
    entry = archive.read("modulekit.seasons.abc")

    assert entry is not None
    assert entry.raw_text == "one"
    assert entry.fetched_at == T1
    assert entry.view == "seasons"


def test_changed_upstream_content_preserves_previous_version(archive):
    """Scorekeepers edit box scores after the fact; the edit must be auditable."""
    put(archive, "modulekit.seasons.abc", "before edit", T1)
    put(archive, "modulekit.seasons.abc", "after edit", T2)

    current = archive.read("modulekit.seasons.abc")
    history = archive.history_for("modulekit.seasons.abc")

    assert current is not None and current.raw_text == "after edit"
    assert len(history) == 1
    assert "20261021T120000Z" in history[0].name
    assert "before edit" in history[0].read_text(encoding="utf-8")


def test_unchanged_content_keeps_no_history(archive):
    put(archive, "modulekit.seasons.abc", "same", T1)
    put(archive, "modulekit.seasons.abc", "same", T2)

    assert archive.history_for("modulekit.seasons.abc") == []


def test_iter_entries_filters_by_view_and_skips_history(archive):
    put(archive, "modulekit.seasons.a", "s1", T1)
    put(archive, "modulekit.seasons.a", "s2", T2)  # creates a history entry
    put(archive, "modulekit.roster.b", "r", T1, view="roster")

    seasons = list(archive.iter_entries("modulekit", "seasons"))

    assert [e.raw_text for e in seasons] == ["s2"]


def test_corrupt_entry_reads_as_missing(archive):
    put(archive, "modulekit.seasons.abc", "one", T1)
    archive.path_for("modulekit.seasons.abc").write_text("{ truncated", encoding="utf-8")

    assert archive.read("modulekit.seasons.abc") is None
    assert not archive.path_for("modulekit.seasons.abc").exists()
