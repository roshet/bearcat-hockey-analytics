"""The static export (M7): every stored page as a file the web app reads directly.

Nothing on the site is computed per request -- the API only ever served stored
JSON -- so the deployed site is plain files: no server, no database. The export
writes the stored payloads verbatim (derived pages only, never raw feed
responses) and swaps the new set in only once it is complete, so a failure
mid-way leaves the previous export untouched.

Layout, under ``out``:

- ``index.json``: current season, the seasons with pages, when built, and the
  last game counted;
- ``<season>/teams.json``: the team list (what ``/api/teams`` returns);
- ``<season>/scouting/<team>.json``, ``<season>/special-teams/<team>.json``;
- ``<season>/rankings.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from bca.db import schema as db
from bca.ingest.endpoints import CURRENT_MENS_SEASON_ID

DEFAULT_OUT = Path(__file__).parents[2] / "web" / "public" / "data"
#: Windows refuses to rename a folder while another process (a dev server's file
#: watcher, the indexer) holds it for a moment: retry for up to ~5 seconds.
RETRIES, RETRY_DELAY = 25, 0.2
#: Left out of the digest, so a rebuild with unchanged pages isn't a change.
INDEX = "index.json"


class ExportSummary(BaseModel):
    seasons: list[int]
    files: int
    data_through: date | None
    #: sha256 over every page's content, build times left out (see ``_digest``).
    digest: str


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _rename(src: Path, dst: Path) -> None:
    for attempt in range(RETRIES):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)


def _digest(root: Path) -> str:
    """What the pages say, not when they were built: each stored page carries its own
    ``built_at``, so a nightly rebuild with no new games must still hash the same."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == INDEX:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop("built_at", None)
        h.update(rel.encode())
        h.update(json.dumps(data, sort_keys=True).encode())
    return h.hexdigest()


def _fill(session: Session, root: Path, now: datetime) -> tuple[list[int], int, date | None]:
    files = 0
    seasons: set[int] = set()
    by_season: dict[int, list[dict[str, object]]] = {}
    for report in session.scalars(select(db.StoredScoutingReport)
                                  .order_by(db.StoredScoutingReport.team)):
        seasons.add(report.season_id)
        by_season.setdefault(report.season_id, []).append(
            {"team_id": report.team_id, "team": report.team, "division": report.division})
        _write(root / str(report.season_id) / "scouting" / f"{report.team_id}.json",
               json.loads(report.payload))
        files += 1
    for season_id, teams in by_season.items():
        _write(root / str(season_id) / "teams.json", teams)
        files += 1
    for detail in session.scalars(select(db.StoredTeamDetailReport)):
        seasons.add(detail.season_id)
        _write(root / str(detail.season_id) / "special-teams" / f"{detail.team_id}.json",
               json.loads(detail.payload))
        files += 1
    for rankings in session.scalars(select(db.StoredRankingsReport)):
        seasons.add(rankings.season_id)
        _write(root / str(rankings.season_id) / "rankings.json", json.loads(rankings.payload))
        files += 1

    through = session.scalar(select(func.max(db.Game.game_date)).where(
        db.Game.season_id == CURRENT_MENS_SEASON_ID, db.Game.is_final))
    _write(root / INDEX, {
        "current_season": CURRENT_MENS_SEASON_ID,
        "seasons": sorted(seasons),
        "built_at": now.isoformat(),
        "data_through": through.isoformat() if through else None,
    })
    return sorted(seasons), files + 1, through


def export_static(sessions: sessionmaker[Session], out: Path = DEFAULT_OUT,
                  now: datetime | None = None) -> ExportSummary:
    """Write every stored page under ``out``, replacing the previous export whole."""
    now = now or datetime.now(UTC)
    tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
    old = out.with_name(f"{out.name}.old-{os.getpid()}")
    # Folders an earlier export couldn't remove (a hard kill, a lock Windows held): Vite
    # copies everything under public/ into the build, so they'd ship and pile up.
    for stale in out.parent.glob(f"{out.name}.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)
    for stale in out.parent.glob(f"{out.name}.old-*"):
        shutil.rmtree(stale, ignore_errors=True)
    try:
        with sessions() as session:
            seasons, files, through = _fill(session, tmp, now)
        digest = _digest(tmp)
        if out.exists():
            _rename(out, old)
        _rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if old.exists() and not out.exists():
            _rename(old, out)
        raise
    shutil.rmtree(old, ignore_errors=True)
    return ExportSummary(seasons=seasons, files=files, data_through=through, digest=digest)
