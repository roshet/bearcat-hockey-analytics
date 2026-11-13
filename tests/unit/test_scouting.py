"""The scouting report, on real games: UC's 2026-27 schedule plus both UC-Kentucky games.

In this fixture world Kentucky has exactly two games, both against UC
(33157: Kentucky 4-3 at home; 33171: Kentucky 10-4 in Cincinnati), so every
Kentucky number below can be checked by hand against those two box scores.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from bca.analytics.scouting import (
    RECENT_STARTS,
    TOP_INFRACTIONS,
    ScoutingReport,
    build_reports,
    display_name,
    infraction_name,
    points_ranks,
)
from bca.analytics.special_teams import run_special_teams
from bca.audit.run import run_audit
from bca.ingest import endpoints
from bca.ingest.archive import RawArchive
from bca.ingest.normalize import Normalizer
from tests.unit.test_normalize import UC, archive_payload, build_archive, payload

KENTUCKY = 346
NOW = datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture
def archive(tmp_path) -> RawArchive:
    archive = build_archive(tmp_path / "cache")
    for game_id in (33157, 33171, 32130, 31980):
        archive_payload(archive, endpoints.game_summary(game_id),
                        payload(f"game_summary_{game_id}"))
    return archive


@pytest.fixture
def kentucky(archive, sessions: sessionmaker[Session]) -> ScoutingReport:
    Normalizer(sessions, archive).run()
    run_audit(sessions)
    run_special_teams(sessions)
    with sessions() as session:
        [report] = build_reports(session, 73, [KENTUCKY], now=NOW)
    return report


# --- the real games --------------------------------------------------------------------


def test_record_and_banner(kentucky):
    season = kentucky.current
    assert kentucky.team == "University of Kentucky"
    assert (season.record.gp, season.record.w, season.record.gf, season.record.ga) == (2, 2, 14, 7)
    assert (season.quality.full_box_scores, season.quality.special_teams_games) == (2, 2)
    assert any("Only 2 games" in note for note in season.quality.notes)


def test_special_teams_come_from_the_simulator(kentucky):
    st = kentucky.current.special_teams
    # 33157: 5 opportunities, 0 goals; 33171: 7 opportunities, 5 goals.
    assert (st.pp_opportunities, st.pp_goals) == (12, 5)
    # UC had 3 + 6 opportunities and scored 0 + 2.
    assert (st.pk_opportunities, st.pp_goals_against) == (9, 2)
    assert st.pp_pct == pytest.approx(5 / 12, abs=1e-4)
    assert st.pk_pct == pytest.approx(1 - 2 / 9, abs=1e-4)
    assert st.pp_minutes_per_game == pytest.approx((600 + 576) / 60 / 2, abs=0.01)


def test_head_to_head_is_from_the_opponents_side(kentucky):
    h2h = [(g.game_id, g.home, g.gf, g.ga, g.result) for g in kentucky.head_to_head]
    assert h2h == [(33171, False, 10, 4, "W"), (33157, True, 4, 3, "W")]


def test_period_goals_add_up_to_the_score(kentucky):
    rows = kentucky.current.by_period.rows
    assert sum(r.gf for r in rows) == 14 and sum(r.ga for r in rows) == 7


def test_scorers_and_goalies_come_from_the_box_scores(kentucky):
    scoring = kentucky.current.scoring
    assert scoring.goals == 14
    assert sum(s.g for s in scoring.top) <= 14
    goalies = kentucky.current.goaltending.goalies
    assert sum(g.starts for g in goalies) == 2
    assert all(g.sv_pct is None or 0 <= g.sv_pct <= 1 for g in goalies)


def test_recent_starts_are_newest_first_one_per_game(kentucky):
    starts = kentucky.current.goaltending.recent_starts
    # Both UC-Kentucky games: 33171 (Sun Oct 11, away) then 33157 (Sat Oct 10, home).
    assert [(s.game_id, s.weekday, s.home) for s in starts] == [
        (33171, "Sun", False), (33157, "Sat", True)
    ]
    assert len(starts) <= RECENT_STARTS
    assert sum(g.starts for g in kentucky.current.goaltending.goalies) == len(starts)


def test_discipline_names_infractions_and_accounts_for_every_penalty(kentucky):
    d = kentucky.current.discipline
    assert 0 < len(d.infractions) <= TOP_INFRACTIONS
    assert sum(i.count for i in d.infractions) + d.other_infractions == round(
        d.infractions_per_game * 2
    )
    counts = [i.count for i in d.infractions]
    assert counts == sorted(counts, reverse=True)
    assert all(i.per_game == round(i.count / 2, 2) for i in d.infractions)


def test_the_league_rate_covers_every_team_in_the_full_box_scores(kentucky):
    """In this fixture world the league is 8 full box scores (UC's season plus
    the two UC-Kentucky games): every infraction Kentucky took has a league rate."""
    for i in kentucky.current.discipline.infractions:
        assert i.league_per_game > 0


def test_uc_never_appears_as_a_comparison(kentucky):
    """Opponent profile: UC is only ever in the head-to-head section."""
    for rank in kentucky.current.ranks:
        assert "cincinnati" not in rank.metric.lower()
    with_uc = [g for g in kentucky.current.form.last5 if "Cincinnati" in g.opponent]
    assert len(with_uc) == 2  # as an opponent in their results, which is fine


def test_uc_own_report_has_no_head_to_head(archive, sessions):
    Normalizer(sessions, archive).run()
    run_audit(sessions)
    run_special_teams(sessions)
    with sessions() as session:
        [report] = build_reports(session, 73, [UC], now=NOW)
    assert report.head_to_head == []


def test_a_non_md1_team_is_refused(archive, sessions):
    Normalizer(sessions, archive).run()
    with sessions() as session, pytest.raises(ValueError, match="not an MD1 team"):
        build_reports(session, 73, [534], now=NOW)  # "MD1 TBD", a placeholder, no team


# --- pure pieces ---------------------------------------------------------------------------

#: Every penalty description that appears 20+ times across 2020-21..2026-27.
FREQUENT_DESCRIPTIONS = [
    "Roughing", "Tripping", "Cross Checking", "Slashing", "Interference", "Hooking",
    "Holding", "High Sticking", "10-Minute Misconduct", "Boarding",
    "Unsportsmanlike Conduct/Player to Player MINOR", "Elbowing", "Bench Minor-Too Many Men",
    "Checking From Behind", "Bench Minor-Unsportsmanlike Conduct", "Game Misconduct",
    "Fighting-Major- w/Disqualification DQ", "Delay Of Game", "Charging", "Kneeing",
    "Indirect Contact to the Head", "Unsportsmanlike Conduct/Player to Official MISCONDUCT",
    "Major-Contact/Hit To The Head w/Game Misconduct", "Goalie Interference",
    "Major-Grab Face Mask w/Game Misconduct", "Major-Boarding", "Major-Contact to the Head",
    "Diving/Embellishment", "Major-Checking from Behind", "Bench Minor-Coach",
    "Abuse of Official-Misconduct", "Major-Cross Checking", "Major-Grab Face Mask",
    "Major-Roughing w/Game Misconduct", "Facemask", "Major-Spearing w/Game Misconduct",
    "Major-Roughing", "Holding The Stick", "Major-Grab Face Mask w/Disqualification DQ",
    "Checking", "Major-Slashing", "Disqualification w/Major",
    "Major-Contact/Hit To The Head w/Disqualification DQ", "Roughing - Hitting After Whistle",
    "Major-Cross Checking w/Game Misconduct",
    "Unsportsmanlike Conduct/Player to Player GAME MISCONDUCT",
    "Bench Game Misconduct-Unsportsmanlike Conduct", "Bench Minor-Delay Of Game",
    "Major-Boarding w/Game Misconduct", "Major-Spearing w/Disqualification DQ",
    "Major-Checking from Behind w/Game Misconduct",
    "Unsportsmanlike Conduct/Player to Official GAME MISCONDUCT",
    "Abuse of Official-Game Misconduct", "Game Misconduct-Coach", "UnSportsmanlike-Taunting",
    "Major-Elbowing", "Major-Charging", "Bench Minor-Abuse of Official",
    "Unsportsmanlike Conduct/Player to Official DQ", "Major-Butt Ending w/Game Misconduct",
    "Major-Kneeing", "Major-Kneeing w/Game Misconduct", "Major-High Sticking",
    "Checking From Behind w/Misconduct", "Penalty Shot-Tripping", "Major-Interference",
    "Major-Slashing w/Game Misconduct", "Faceoff violation", "Clearing the Area of a Fight",
    "Closing Hand On Puck", "Major-Cross Checking w/Disqualification DQ",
    "Major-Charging w/Game Misconduct",
]


def test_every_frequent_description_gets_a_plain_name():
    """No feed artefacts survive: no 'Major-', 'w/...', 'DQ' or shouting caps."""
    for description in FREQUENT_DESCRIPTIONS:
        # Pure misconducts are always 10-minute penalties in the data.
        lower = description.lower()
        pure_misconduct = "misconduct" in lower and "w/" not in lower and "major" not in lower
        name = infraction_name(10 if pure_misconduct else 2, description)
        assert not any(junk in name for junk in ("Major-", "w/", " DQ", "MINOR", "MISCONDUCT"))
        assert name[0].isupper()


@pytest.mark.parametrize(
    ("minutes", "description", "name"),
    [
        (2, "Roughing", "Roughing"),
        (2, "Roughing - Hitting After Whistle", "Roughing"),
        (15, "Major-Roughing w/Game Misconduct", "Roughing"),
        (5, "Major-Cross Checking", "Cross checking"),
        (15, "Major-Cross Checking w/Disqualification DQ", "Cross checking"),
        (2, "Holding The Stick", "Holding the stick"),
        (2, "Holding", "Holding"),
        (2, "Penalty Shot-Tripping", "Tripping"),
        (2, "Bench Minor-Too Many Men", "Too many men"),
        (2, "Unsportsmanlike Conduct/Player to Player MINOR", "Unsportsmanlike conduct"),
        (15, "Major-Contact/Hit To The Head w/Game Misconduct", "Head contact"),
        (15, "Fighting-Major- w/Disqualification DQ", "Fighting"),
        (10, "10-Minute Misconduct", "Misconduct"),
        (10, "Unsportsmanlike Conduct/Player to Player GAME MISCONDUCT", "Game misconduct"),
        (2, "Bench Minor-Coach", "Bench minor (coach)"),
    ],
)
def test_infraction_name_merges_the_feeds_wordings(minutes, description, name):
    assert infraction_name(minutes, description) == name


def test_display_name_drops_the_division_prefix():
    assert display_name("MD1 University of Kentucky") == "University of Kentucky"
    assert display_name("M1 University of Jamestown") == "University of Jamestown"
    assert display_name("Kent State") == "Kent State"


def test_points_ranks_share_a_place_on_ties():
    assert points_ranks({1: 10, 2: 8, 3: 8, 4: 5}) == {
        1: (1, False), 2: (2, True), 3: (2, True), 4: (4, False)}


def test_scorers_carry_their_league_rank(kentucky):
    scoring = kentucky.current.scoring
    ranks = [s.md1_rank for s in scoring.top]
    assert ranks and all(r is not None and 1 <= r <= scoring.md1_skaters for r in ranks)
    assert ranks[0] == min(r for r in ranks if r is not None)
    assert kentucky.prior is None or all(s.md1_rank is None for s in kentucky.prior.scoring.top)


def test_scorers_carry_the_points_their_rank_is_on(kentucky):
    """The rank is on points for every MD1 team a player dressed for; the table's P is this
    team's only. In this fixture world nobody changed teams, so the two agree."""
    for s in kentucky.current.scoring.top:
        assert s.md1_points == s.p
    assert kentucky.prior is None or all(s.md1_points is None for s in kentucky.prior.scoring.top)
