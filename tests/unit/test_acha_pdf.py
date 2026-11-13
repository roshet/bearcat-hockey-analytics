"""The ACHA's official M1 ranking PDFs: archived, parsed, stored (M6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from bca.analytics.acha_pdf import (
    PublishedRow,
    describe_pick_up,
    fetch_pdf,
    map_names,
    parse_pdf,
    pick_up,
    season_code,
    store_published,
)
from bca.config import ConfigurationError, Settings
from bca.db import schema as db
from tests.fixture_data import fixture_file, fixture_json


def final_2526() -> Path:
    return fixture_file("rankings/2526-final.pdf")


URL = "http://www.achahockey.org/wp-content/uploads/sites/2/2026/08/2526-final.pdf"


@pytest.fixture
def names() -> dict[int, str]:
    raw = fixture_json("rankings/md1_names_2526.json")
    return {int(k): v for k, v in raw.items()}


def make_settings(tmp_path: Path, email: str = "maintainer@bca.test") -> Settings:
    return Settings(database_url="postgresql+psycopg://unused@localhost/unused",
                    cache_dir=tmp_path / "cache", request_delay_seconds=0.0,
                    contact_email=email)


def test_the_final_2025_26_pdf_parses_to_81_ranked_rows():
    as_of, rows = parse_pdf(final_2526())
    assert as_of == date(2026, 3, 17) and len(rows) == 81
    assert rows[0] == PublishedRow(1, "Minot State", 1, 1, 1.0, "MCH")
    uc = next(r for r in rows if r.team == "Cincinnati")
    assert uc == PublishedRow(39, "Cincinnati", 39, 41, 40.0, "Independent")
    assert [r.rank for r in rows] == list(range(1, 82))


def test_league_pages_are_not_read_twice():
    _, rows = parse_pdf(final_2526())
    assert len({r.team for r in rows}) == 81


def test_every_pdf_name_maps_to_one_team(names):
    _, rows = parse_pdf(final_2526())
    mapping = map_names(rows, names)
    assert len(mapping) == 81 and len(set(mapping.values())) == 81
    assert mapping["Cincinnati"] == 265
    assert names[mapping["Arizona"]] == "MD1 University of Arizona"
    assert names[mapping["Arizona State"]] == "MD1 Arizona State University"
    assert names[mapping["Navy"]] == "MD1 United States Naval Academy"


def test_an_unknown_pdf_name_is_an_error(names):
    with pytest.raises(ValueError, match="Atlantis"):
        map_names([PublishedRow(1, "Atlantis", 1, 1, 1.0, "MCH")], names)


def test_fetch_refuses_without_a_contact_email(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"%PDF")

    with pytest.raises(ConfigurationError):
        fetch_pdf(URL, 60, make_settings(tmp_path, email=""), httpx.MockTransport(handler))
    assert calls == []


def test_fetch_identifies_us_and_archives_the_raw_bytes(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"%PDF-1 one")

    path = fetch_pdf(URL, 60, make_settings(tmp_path), httpx.MockTransport(handler))
    assert path == tmp_path / "cache" / "acha_rankings" / "60" / "2526-final.pdf"
    assert path.read_bytes() == b"%PDF-1 one"
    assert "maintainer@bca.test" in seen[0].headers["user-agent"]


def test_a_changed_download_keeps_the_previous_file(tmp_path):
    body = {"v": b"%PDF-1 one"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body["v"])

    settings = make_settings(tmp_path)
    path = fetch_pdf(URL, 60, settings, httpx.MockTransport(handler))
    fetch_pdf(URL, 60, settings, httpx.MockTransport(handler))
    history = path.parent / "history"
    assert not history.exists() or list(history.iterdir()) == []

    body["v"] = b"%PDF-1 two"
    fetch_pdf(URL, 60, settings, httpx.MockTransport(handler))
    kept = list(history.iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == b"%PDF-1 one"
    assert path.read_bytes() == b"%PDF-1 two"


def test_store_published_is_idempotent(sessions, names):
    with sessions() as session:
        for _ in range(2):
            as_of = store_published(session, 60, final_2526(), names)
        session.commit()
        count = session.scalar(select(func.count()).select_from(db.PublishedRanking))
        uc = session.get(db.PublishedRanking, (60, date(2026, 3, 17), 265))
    assert as_of == date(2026, 3, 17) and count == 81
    assert uc is not None and (uc.rank, uc.rank_a, uc.rank_b, uc.league) == (39, 39, 41,
                                                                            "Independent")
    assert uc.source_file == "2526-final.pdf"


def test_the_as_of_date_can_be_overridden(sessions, names):
    with sessions() as session:
        as_of = store_published(session, 60, final_2526(), names, as_of_override=date(2026, 3, 8))
        session.commit()
    assert as_of == date(2026, 3, 8)


# --- picking up PDFs the user downloaded ---------------------------------------------


def _inbox(tmp_path: Path, files: dict[str, bytes]) -> Path:
    inbox = tmp_path / "Downloads"
    inbox.mkdir()
    for name, content in files.items():
        (inbox / name).write_bytes(content)
    return inbox


def test_a_downloaded_pdf_is_archived_unchanged_and_stored_once(tmp_path, sessions, names):
    settings = make_settings(tmp_path)
    inbox = _inbox(tmp_path, {"2526computer-average-final.pdf": final_2526().read_bytes()})
    with sessions() as session:
        first = pick_up(session, 60, inbox, "2526", names, settings)
        session.commit()
        again = pick_up(session, 60, inbox, "2526", names, settings)
        count = session.scalar(select(func.count()).select_from(db.PublishedRanking))
    archived = tmp_path / "cache" / "acha_rankings" / "60" / "2526computer-average-final.pdf"
    assert [(r.name, r.as_of, r.error) for r in first] == [
        ("2526computer-average-final.pdf", date(2026, 3, 17), None)]
    assert archived.read_bytes() == final_2526().read_bytes()
    assert again == [] and count == 81
    assert (inbox / "2526computer-average-final.pdf").exists()  # Downloads is left alone


def test_a_second_copy_of_the_same_download_is_not_loaded_again(tmp_path, sessions, names):
    settings = make_settings(tmp_path)
    pdf = final_2526().read_bytes()
    inbox = _inbox(tmp_path, {"2526computer-average-final.pdf": pdf})
    with sessions() as session:
        pick_up(session, 60, inbox, "2526", names, settings)
        session.commit()
        (inbox / "2526computer-average-final (1).pdf").write_bytes(pdf)
        assert pick_up(session, 60, inbox, "2526", names, settings) == []


def test_other_seasons_and_other_files_are_ignored(tmp_path, sessions, names):
    pdf = final_2526().read_bytes()
    inbox = _inbox(tmp_path, {"2425computer_average_02-23.pdf": pdf, "2526schedule.pdf": pdf,
                              "2526computer-average-notes.txt": b"x"})
    with sessions() as session:
        assert pick_up(session, 60, inbox, "2526", names, make_settings(tmp_path)) == []
        assert pick_up(session, 60, tmp_path / "missing", "2526", names,
                       make_settings(tmp_path)) == []


def test_a_broken_download_is_reported_not_raised(tmp_path, sessions, names):
    inbox = _inbox(tmp_path, {"2526computer-average-bad.pdf": b"not a pdf at all"})
    with sessions() as session:
        results = pick_up(session, 60, inbox, "2526", names, make_settings(tmp_path))
        count = session.scalar(select(func.count()).select_from(db.PublishedRanking))
    assert len(results) == 1 and results[0].error and results[0].as_of is None
    assert count == 0
    assert "could not store 2526computer-average-bad.pdf" in describe_pick_up(results)
    assert describe_pick_up([]) == "rankings: nothing new in the inbox"


def test_the_season_code_comes_from_its_start_date(sessions):
    with sessions() as session:
        session.add(db.Season(season_id=73, name="2026-2027", short_name="26-27",
                              is_playoff=False, start_date=date(2026, 8, 1)))
        session.flush()
        assert season_code(session, 73) == "2627"
        assert season_code(session, 999) is None


# --- checking the rebuild against the ACHA (Task 3) ----------------------------------

from bca.analytics.acha_pdf import compare  # noqa: E402
from bca.analytics.rankings import RankingSeason, rank_table  # noqa: E402
from tests.unit.test_rankings import game  # noqa: E402

AS_OF = date(2025, 11, 2)


def _season(games: list[db.Game], teams: list[int]) -> RankingSeason:
    return RankingSeason(season_id=60, label="2025-26", games=games, upcoming=[],
                         md1=set(teams), names={t: f"Team {t}" for t in teams})


def _published(order: list[int], as_of: date = AS_OF) -> list[db.PublishedRanking]:
    return [db.PublishedRanking(season_id=60, as_of=as_of, team_id=t, rank=i + 1, rank_a=i + 1,
                                rank_b=i + 1, avg=float(i + 1), league="X", source_file="f.pdf")
            for i, t in enumerate(order)]


def _ladder(n: int, on: date = date(2025, 10, 1)) -> list[db.Game]:
    """Team 1 beats everyone, team 2 everyone but 1, ...: ranks 1..n, no ties."""
    return [game(a, b, 3 + (b - a) % 3, 0, on=on) for a in range(1, n + 1)
            for b in range(a + 1, n + 1)]


def test_compare_counts_exact_and_within_2():
    teams = list(range(1, 31))
    order = list(teams)
    order[0], order[1] = order[1], order[0]      # off by 1: within 2
    order[9], order[13] = order[13], order[9]    # off by 4: a difference
    check = compare(_season(_ladder(30), teams), _published(order))
    assert (check.teams, check.agree_exact, check.within_2) == (30, 26, 28)
    assert sorted(d.team_id for d in check.differences) == [10, 14]
    assert all(d.cause is None for d in check.differences)
    assert check.top24_match


def test_a_top24_swap_across_the_line_is_not_a_match():
    teams = list(range(1, 31))
    order = list(teams)
    order[21], order[27] = order[27], order[21]  # 22 and 28 trade places across 24th
    assert not compare(_season(_ladder(30), teams), _published(order)).top24_match


def test_a_tie_at_24th_counts_as_a_top24_match():
    teams = list(range(1, 31))
    games = _ladder(30)
    # 24 and 25 never meet and play everyone else identically: a true tie.
    games = [g for g in games if not ({g.home_team_id, g.visiting_team_id} & {24, 25})]
    for other in range(1, 31):
        if other in (24, 25):
            continue
        for t in (24, 25):
            games.append(game(other, t, 2, 1) if other < 24 else game(t, other, 2, 1))
    rows = {r.team_id: r for r in rank_table(games, set(teams), {})}
    assert (rows[24].rating_a, rows[24].rating_b) == (rows[25].rating_a, rows[25].rating_b)
    ours = sorted(teams, key=lambda t: rows[t].rank or 99)
    swapped = list(ours)
    i, j = swapped.index(24), swapped.index(25)
    swapped[i], swapped[j] = swapped[j], swapped[i]
    assert compare(_season(games, teams), _published(swapped)).top24_match


def test_compare_rebuilds_as_of_the_pdf_date_and_reports_our_last_game():
    teams = list(range(1, 31))
    games = _ladder(30, on=date(2025, 10, 25))
    upset = game(30, 1, 9, 0, on=date(2025, 11, 5))  # after the PDF: ignored
    check = compare(_season([*games, upset], teams), _published(teams))
    assert check.agree_exact == 30
    assert check.as_of == AS_OF and check.our_games_through == date(2025, 10, 25)


def test_a_known_cause_is_attached_to_its_difference(monkeypatch):
    from bca.analytics import acha_pdf

    teams = list(range(1, 31))
    order = list(teams)
    order[9], order[13] = order[13], order[9]
    monkeypatch.setitem(acha_pdf.KNOWN_CAUSES, (60, 10), "a game we don't have")
    check = compare(_season(_ladder(30), teams), _published(order))
    assert {d.team_id: d.cause for d in check.differences} == {10: "a game we don't have",
                                                               14: None}


def test_a_row_whose_last_week_is_not_ranked_still_parses():
    from bca.analytics.acha_pdf import parse_line

    assert parse_line("8 Nevada-Las Vegas 10 4 7.0 WCHL NR") == PublishedRow(
        8, "Nevada-Las Vegas", 10, 4, 7.0, "WCHL")
    assert parse_line("39 Cincinnati 39 41 40.0 Independent 36") == PublishedRow(
        39, "Cincinnati", 39, 41, 40.0, "Independent")
    assert parse_line("# Team A B AVG League Last Week") is None


def test_a_pdf_without_the_sorted_by_ranking_footer_reads_its_ranking_pages():
    # 2024-25's PDFs mark the ranking pages only by their "Last Week" column.
    as_of, rows = parse_pdf(fixture_file("rankings/2425-0223.pdf"))
    assert as_of == date(2025, 2, 23)
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0] == PublishedRow(1, "Minot State", 1, 1, 1.0, "MCH")
    assert len({r.team for r in rows}) == len(rows)


def test_a_ranked_team_outside_our_md1_set_maps_through_the_fallback(names):
    rows = [PublishedRow(1, "Cincinnati", 1, 1, 1.0, "X"),
            PublishedRow(2, "Concordia Ann Arbor", 2, 2, 2.0, "X")]
    everyone = {**names, 569: "MD2 Concordia University-Ann Arbor"}
    assert map_names(rows, names, fallback=everyone) == {"Cincinnati": 265,
                                                         "Concordia Ann Arbor": 569}
    with pytest.raises(ValueError, match="Concordia"):
        map_names(rows, names)


def test_compare_lists_a_team_the_acha_ranks_but_we_do_not_hold_as_md1():
    teams = list(range(1, 31))
    published = _published([*teams, 77])
    check = compare(_season(_ladder(30), teams), published)
    assert [(d.team_id, d.rebuilt, d.cause) for d in check.differences] == [
        (77, None, "not an MD1 team in the league feed this season")]


def test_a_skipped_ranking_row_is_an_error_not_a_gap():
    from bca.analytics.acha_pdf import parse_pages

    page = ("as of 11-02-25\n# Team A B AVG League Last Week\n1 Ohio 2 2 2.0 Independent NR\n"
            "T-2 Niagara 1 3 2.0 Independent NR\n3 Minot State 4 1 2.5 MCH NR\n")
    with pytest.raises(ValueError, match="ranks 1-3 with 2 missing"):
        parse_pages([page], "x.pdf")
