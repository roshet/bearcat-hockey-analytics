"""The on-disk archive of raw feed responses.

Every response is persisted verbatim before anything parses it. That gives us
three things, in order of importance:

1. Normalization can be re-run offline after a parser fix, without re-fetching
   ~9,000 resources from a volunteer league's server.
2. Upstream edits are auditable. Scorekeepers amend box scores after the fact;
   when a refetch returns different content, the previous envelope is moved to
   ``history/`` rather than overwritten.
3. The analytics layer is testable against real data with no network at all.

The archive does no network I/O, so -- unlike ``HockeyTechClient`` -- building
one needs no contact address.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HISTORY_DIR = "history"


@dataclass(frozen=True, slots=True)
class ArchivedResponse:
    """One archived response, as stored on disk."""

    feed: str
    params: dict[str, str]
    payload: Any
    fetched_at: datetime
    raw_text: str
    path: Path = field(compare=False)

    @property
    def view(self) -> str:
        return self.params.get("view") or self.params.get("tab") or ""


class RawArchive:
    """Read and write archived feed responses, keyed by request cache key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, cache_key: str) -> Path:
        return self.root / f"{cache_key}.json"

    def read(self, cache_key: str) -> ArchivedResponse | None:
        """Return the archived response, or None if absent or unreadable.

        A corrupt entry is deleted: it must never be fatal, and the caller will
        refetch it.
        """
        path = self.path_for(cache_key)
        if not path.exists():
            return None
        try:
            return _load(path)
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("discarding unreadable archive entry %s: %s", path.name, exc)
            path.unlink(missing_ok=True)
            return None

    def write(
        self,
        cache_key: str,
        *,
        identity: str,
        feed: str,
        params: dict[str, str],
        fetched_at: datetime,
        payload: Any,
        raw_text: str,
    ) -> None:
        """Archive a response, preserving any differing previous version."""
        path = self.path_for(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._preserve_previous(path, raw_text)

        envelope = {
            "identity": identity,
            "feed": feed,
            "params": params,
            "fetched_at": fetched_at.isoformat(),
            "payload": payload,
            "raw_text": raw_text,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(envelope), encoding="utf-8")
        tmp.replace(path)

    def iter_entries(self, feed: str, view: str) -> Iterator[ArchivedResponse]:
        """Every current (non-history) entry for one feed view, in stable order."""
        for path in sorted(self.root.glob(f"{feed}.{view}.*.json")):
            try:
                yield _load(path)
            except (OSError, KeyError, ValueError) as exc:
                logger.warning("skipping unreadable archive entry %s: %s", path.name, exc)

    def history_for(self, cache_key: str) -> list[Path]:
        """Previous versions of one resource, oldest first."""
        return sorted((self.root / HISTORY_DIR).glob(f"{cache_key}.*.json"))

    def _preserve_previous(self, path: Path, new_raw_text: str) -> None:
        if not path.exists():
            return
        try:
            previous = _load(path)
        except (OSError, KeyError, ValueError):
            return  # unreadable; nothing worth preserving
        if previous.raw_text == new_raw_text:
            return
        stamp = previous.fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.root / HISTORY_DIR / f"{path.stem}.{stamp}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        logger.info("upstream content changed; kept previous version as %s", target.name)


def _load(path: Path) -> ArchivedResponse:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    return ArchivedResponse(
        feed=envelope["feed"],
        params=envelope["params"],
        payload=envelope["payload"],
        fetched_at=datetime.fromisoformat(envelope["fetched_at"]),
        raw_text=envelope["raw_text"],
        path=path,
    )
