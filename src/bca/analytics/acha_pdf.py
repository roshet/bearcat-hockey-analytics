"""The ACHA's official M1 computer ranking PDFs: fetched, archived, parsed (M6).

The ACHA posts each week's full ranking as a PDF on achahockey.org/m1-rankings.
We keep them only to check our rebuilt ranking (``analytics/rankings.py``)
against: where the two disagree, a game is usually missing or entered
differently, which is worth knowing (convention 5).

Fetching follows the feed's rules: a real contact address in the User-Agent,
the request delay, one request at a time, and the raw bytes archived before
anything parses them (conventions 2 and 4).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bca.analytics.acha_names import PDF_ALIASES
from bca.analytics.rankings import (
    LINE,
    RankingSeason,
    load_ranking_season,
    rank_table,
    ranked_games,
)
from bca.config import Settings, get_settings, require_contact_email
from bca.db import schema as db

ARCHIVE_DIR = "acha_rankings"
HISTORY_DIR = "history"

_AS_OF = re.compile(r"as of (\d{2})-(\d{2})-(\d{2})")
#: rank, team, A, B, average, league, then last week's rank (a number or "NR").
_ROW = re.compile(r"^(\d+) (.+?) (\d+) (\d+) (\d+(?:\.\d+)?) (\S+)(?: (?:\d+|NR))?$")
#: The by-ranking pages carry a "Last Week" column; the by-league pages do not.
_RANKING_PAGE = "Last Week"

_last_request_at: float | None = None

#: Why a team's rebuilt rank differs from the ACHA's by 3 or more, where we know:
#: (season_id, team_id) -> cause. Filled in by checking the games by hand
#: (docs/data-sources.md, "Rankings (M6)"); anything else is printed "unexplained".
KNOWN_CAUSES: dict[tuple[int, int], str] = {}
#: The cause given for a team the ACHA ranks but the feed doesn't list as MD1.
NOT_MD1 = "not an MD1 team in the league feed this season"
#: A rebuilt rank this far from the official one is listed as a difference.
DIFFERENCE = 3


@dataclass(frozen=True, slots=True)
class PublishedRow:
    rank: int
    team: str
    rank_a: int
    rank_b: int
    avg: float
    league: str


def archive_path(settings: Settings, season_id: int, url: str) -> Path:
    name = Path(urlparse(url).path).name
    return settings.resolved_cache_dir / ARCHIVE_DIR / str(season_id) / name


def fetch_pdf(url: str, season_id: int, settings: Settings | None = None,
              transport: httpx.BaseTransport | None = None) -> Path:
    """Download one ranking PDF and archive its raw bytes; return the archived path.

    A re-download with different bytes moves the previous file to ``history/``.
    """
    global _last_request_at
    settings = settings or get_settings()
    require_contact_email(settings)  # before any request: we always say who we are
    delay = settings.request_delay_seconds
    if _last_request_at is not None and (wait := delay - (time.monotonic() - _last_request_at)) > 0:
        time.sleep(wait)
    with httpx.Client(timeout=settings.request_timeout_seconds, transport=transport,
                      headers={"User-Agent": settings.user_agent},
                      follow_redirects=True) as client:
        response = client.get(url)
    _last_request_at = time.monotonic()
    response.raise_for_status()

    path = archive_path(settings, season_id, url)
    archive_bytes(path, response.content)
    return path


def archive_bytes(path: Path, content: bytes) -> None:
    """Write raw bytes to the archive; a different earlier copy moves to ``history/``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y%m%dT%H%M%SZ")
        kept = path.parent / HISTORY_DIR / f"{path.stem}.{stamp}{path.suffix}"
        kept.parent.mkdir(exist_ok=True)
        path.replace(kept)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def parse_line(line: str) -> PublishedRow | None:
    if m := _ROW.match(line.strip()):
        rank, team, a, b, avg, league = m.groups()
        return PublishedRow(int(rank), team, int(a), int(b), float(avg), league)
    return None


def parse_pages(pages: list[str], name: str) -> tuple[date, list[PublishedRow]]:
    """The "as of" date and the ranking rows from a PDF's page texts.

    The PDF lists every team twice (by ranking, then by league); only the
    by-ranking pages are read. Their ranks must run 1..N without a gap: a line
    the parser can't read is an error, never a silently missing team.
    """
    as_of: date | None = None
    rows: list[PublishedRow] = []
    for text in pages:
        if as_of is None and (m := _AS_OF.search(text)):
            month, day, year = (int(x) for x in m.groups())
            as_of = date(2000 + year, month, day)
        if _RANKING_PAGE not in text:
            continue
        rows.extend(row for line in text.splitlines() if (row := parse_line(line)))
    if as_of is None:
        raise ValueError(f"{name}: no 'as of' date found")
    if not rows:
        raise ValueError(f"{name}: no ranking rows found")
    ranks = {r.rank for r in rows}
    top = max(ranks)
    if len(rows) != top or ranks != set(range(1, top + 1)):
        missing = ", ".join(map(str, sorted(set(range(1, top + 1)) - ranks))) or "duplicates"
        raise ValueError(f"{name}: ranks 1-{top} with {missing} missing; "
                         "a line the parser can't read")
    return as_of, rows


def parse_pdf(path: Path) -> tuple[date, list[PublishedRow]]:
    """The ranking's "as of" date and its rows (see ``parse_pages``)."""
    return parse_pages([page.extract_text() or "" for page in PdfReader(path).pages], path.name)


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", name.lower())) - {"md"}


def map_names(rows: list[PublishedRow], names: dict[int, str],
              fallback: dict[int, str] | None = None) -> dict[str, int]:
    """PDF team name -> team_id, by whole-word match; every name must place.

    ``names`` are the season's MD1 teams. A name none of them matches is looked
    up in ``fallback`` (every team we know): the ACHA has ranked a team the
    league feed doesn't list as MD1 that season, which the check then reports.
    The most specific names go first, so "Arizona State" takes Arizona State
    before "Arizona" is matched among the teams left.
    """
    taken: set[int] = set()
    mapping: dict[str, int] = {}
    for row in sorted(rows, key=lambda r: -len(_words(PDF_ALIASES.get(r.team, r.team)))):
        wanted = _words(PDF_ALIASES.get(row.team, row.team))
        for pool in (names, fallback or {}):
            candidates = sorted((len(_words(n) - wanted), t) for t, n in pool.items()
                                if t not in taken and wanted <= _words(n))
            if candidates:
                break
        if not candidates:
            raise ValueError(f"no team for PDF name {row.team!r}; add it to PDF_ALIASES")
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            tied = ", ".join(pool[t] for _, t in candidates[:2])
            raise ValueError(f"PDF name {row.team!r} matches more than one team ({tied}); "
                             "add it to PDF_ALIASES")
        mapping[row.team] = candidates[0][1]
        taken.add(candidates[0][1])
    return mapping


def store_published(session: Session, season_id: int, path: Path, names: dict[int, str],
                    as_of_override: date | None = None,
                    fallback: dict[int, str] | None = None) -> date:
    """Parse an archived PDF into ``published_rankings``, replacing that week's rows."""
    as_of, rows = parse_pdf(path)
    as_of = as_of_override or as_of
    mapping = map_names(rows, names, fallback)
    session.execute(delete(db.PublishedRanking).where(
        db.PublishedRanking.season_id == season_id, db.PublishedRanking.as_of == as_of))
    session.add_all(
        db.PublishedRanking(season_id=season_id, as_of=as_of, team_id=mapping[r.team],
                            rank=r.rank, rank_a=r.rank_a, rank_b=r.rank_b, avg=r.avg,
                            league=r.league, source_file=path.name)
        for r in rows
    )
    session.flush()
    return as_of


# --- picking up PDFs the user downloaded ------------------------------------------------

# Since Nov 2026 achahockey.org answers our downloader with 403, so the user downloads each
# PDF in the browser and the morning run picks it up from their Downloads folder. Nothing
# here sends a request; files in the inbox are only read, never moved or deleted.


@dataclass(frozen=True, slots=True)
class PickedUp:
    name: str
    as_of: date | None
    error: str | None = None


def season_code(session: Session, season_id: int) -> str | None:
    """'2627' for 2026-27: how the ACHA's file names start."""
    season = session.get(db.Season, season_id)
    if season is None or season.start_date is None:
        return None
    year = season.start_date.year
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


def _is_ranking_pdf(path: Path, code: str) -> bool:
    name = path.name.lower()
    return (path.is_file() and name.startswith(code) and name.endswith(".pdf")
            and "computer" in name and "average" in name)


def pick_up(session: Session, season_id: int, inbox: Path, code: str, names: dict[int, str],
            settings: Settings, fallback: dict[int, str] | None = None) -> list[PickedUp]:
    """Archive and store each new ranking PDF in ``inbox``; one result per file handled.

    A file is skipped when identical bytes are already archived and stored, so a PDF left in
    Downloads is loaded once. A file that won't parse is reported, never raised.
    """
    if not inbox.is_dir():
        return []
    folder = settings.resolved_cache_dir / ARCHIVE_DIR / str(season_id)
    stored = set(session.scalars(select(db.PublishedRanking.source_file).distinct()
                                 .where(db.PublishedRanking.season_id == season_id)))
    results: list[PickedUp] = []
    for source in sorted(p for p in inbox.iterdir() if _is_ranking_pdf(p, code)):
        content = source.read_bytes()
        if any(f.name in stored and f.read_bytes() == content
               for f in folder.glob("*.pdf") if f.is_file()):
            continue
        path = folder / source.name
        archive_bytes(path, content)  # archive first (convention 2)
        try:
            results.append(PickedUp(path.name, store_published(session, season_id, path, names,
                                                               fallback=fallback)))
        except (ValueError, PyPdfError) as exc:
            results.append(PickedUp(path.name, None, str(exc)))
    return results


def pick_up_season(session: Session, season_id: int, settings: Settings) -> list[PickedUp]:
    """``pick_up`` from the configured inbox, with the season's code and MD1 names."""
    code = season_code(session, season_id)
    if code is None:
        return []
    season = load_ranking_season(session, season_id)
    names = {t: season.names[t] for t in season.md1}
    return pick_up(session, season_id, settings.rankings_inbox, code, names, settings,
                   fallback=season.names)


def describe_pick_up(results: list[PickedUp]) -> str:
    """One line for the run log: what was stored, what failed, or nothing new."""
    if not results:
        return "rankings: nothing new in the inbox"
    return "rankings: " + "; ".join(
        f"could not store {r.name}: {r.error}" if r.error else f"stored {r.name} (as of {r.as_of})"
        for r in results)


# --- checking the rebuild against the ACHA -------------------------------------------


class RankDifference(BaseModel):
    team_id: int
    team: str
    official: int
    rebuilt: int | None
    cause: str | None


class RankCheck(BaseModel):
    as_of: date
    #: The last game our rebuild counted (on or before ``as_of``).
    our_games_through: date | None
    teams: int
    agree_exact: int
    within_2: int
    #: The same teams above the nationals line; a true tie at 24th still matches.
    top24_match: bool
    differences: list[RankDifference]


def compare(season: RankingSeason, published: list[db.PublishedRanking]) -> RankCheck:
    """Rebuild the ranking as of the PDF's date and compare it, team by team."""
    as_of = published[0].as_of
    games = ranked_games(season.games, season.md1, as_of)
    rows = {r.team_id: r for r in rank_table(games, season.md1, season.names)}
    official = {p.team_id: p.rank for p in published}
    rebuilt = {t: rows[t].rank if t in rows else None for t in official}

    at_line = next((r for r in rows.values() if r.rank == LINE), None)
    above = {t for t, r in rows.items() if r.rank is not None and r.rank <= LINE}
    if at_line is not None:  # teams level with 24th on both ratings are above it too
        above |= {t for t, r in rows.items()
                  if (r.rating_a, r.rating_b) == (at_line.rating_a, at_line.rating_b)}
    official_above = {t for t, rank in official.items() if rank <= LINE}

    def gap(t: int) -> int | None:
        ours = rebuilt[t]
        return None if ours is None else abs(ours - official[t])

    return RankCheck(
        as_of=as_of,
        our_games_through=max((g.game_date for g in games if g.game_date), default=None),
        teams=len(official),
        agree_exact=sum(gap(t) == 0 for t in official),
        within_2=sum((g := gap(t)) is not None and g <= 2 for t in official),
        top24_match=official_above <= above,
        differences=[
            RankDifference(team_id=t, team=season.names.get(t, str(t)), official=official[t],
                           rebuilt=rebuilt[t],
                           cause=KNOWN_CAUSES.get((season.season_id, t))
                           or (NOT_MD1 if t not in season.md1 else None))
            for t in sorted(official, key=lambda t: official[t])
            if (g := gap(t)) is None or g >= DIFFERENCE
        ],
    )
