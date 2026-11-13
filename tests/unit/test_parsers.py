"""Parser tests against payloads archived from the live feed.

Fixture game 33169 is Cincinnati 7-2 Kent State (2026-09-18).
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from bca.ingest.parsers.common import clock_to_seconds, parse_period
from bca.ingest.parsers.game_summary import canary_findings, parse_game_summary
from bca.ingest.parsers.modulekit import (
    parse_roster,
    parse_schedule,
    parse_seasons,
    parse_teams_by_season,
)
from tests.fixture_data import fixture_json


def payload(name: str) -> Any:
    return fixture_json(f"live/{name}.json")


# --- primitives --------------------------------------------------------------


@pytest.mark.parametrize(("clock", "seconds"), [("4:39", 279), ("00:23", 23), ("20:00", 1200)])
def test_clock_is_elapsed_seconds(clock, seconds):
    assert clock_to_seconds(clock) == seconds


@pytest.mark.parametrize("clock", ["", "4:3", "4:60", "1:02:03", "abc"])
def test_unrecognised_clock_raises(clock):
    with pytest.raises(ValueError):
        clock_to_seconds(clock)


def test_overtime_period_is_accepted_but_a_shootout_marker_is_not():
    assert parse_period("4") == 4
    with pytest.raises(ValueError):
        parse_period("SO")


# --- modulekit views ---------------------------------------------------------


def test_seasons_include_every_mens_regular_season():
    seasons = {s.season_id: s for s in parse_seasons(payload("seasons"))}

    for season_id in (1, 10, 21, 34, 46, 60, 73):
        assert season_id in seasons
        assert not seasons[season_id].is_playoff
    assert seasons[73].start_date == date(2026, 8, 1)


def test_md1_rule_finds_81_teams_including_the_sechl():
    teams = parse_teams_by_season(payload("teams_by_season"))
    md1 = {t.team_id for t in teams if t.is_md1}

    assert len(teams) == 462
    assert len(md1) == 81
    assert {52, 408, 265, 346, 347, 356} <= md1


@pytest.mark.parametrize(
    ("division", "is_md1"),
    [
        ("MD1 SECHL", True),
        ("MD1 Independent", True),
        ("Men's Pool A", True),  # D1's name in 2020-21 through 2022-23
        ("MD2 TSCHL", False),
        ("Men's Division 2", False),
        ("Men's Division 3", False),
        ("ACDC", False),
    ],
)
def test_md1_rule_covers_the_pre_conference_division_name(division, is_md1):
    row = {"id": "1", "name": "x", "nickname": "x", "code": "x", "division_id": "1",
           "division_long_name": division}
    assert parse_teams_by_season({"SiteKit": {"Teamsbyseason": [row]}})[0].is_md1 is is_md1


def test_schedule_rows_coerce_types():
    games = {g.game_id: g for g in parse_schedule(payload("team_schedule"))}
    game = games[33169]

    assert (game.home_team_id, game.visiting_team_id) == (265, 386)
    assert (game.home_goals, game.visiting_goals) == (7, 2)
    assert game.is_final and not game.overtime and not game.shootout
    assert game.attendance is None, "blank attendance means not entered, not zero"
    assert sum(g.is_final for g in games.values()) == 10


def test_roster_splits_players_from_the_nested_coach_list():
    roster = parse_roster(payload("team_roster"))

    assert len(roster.players) == 26
    assert {s.role for s in roster.staff} == {"Head Coach", "Assistant Coach"}
    assert [s.person_id for s in roster.staff if s.role == "Head Coach"] == [37954]


def test_roster_never_models_birthdates():
    player = parse_roster(payload("team_roster")).players[0]
    assert not any("birth" in name for name in type(player).model_fields)


# --- game summary ------------------------------------------------------------


@pytest.fixture
def summary():
    return parse_game_summary(payload("game_summary"))


def test_box_score_totals(summary):
    assert summary.details.game_id == 33169
    assert (summary.home.info.team_id, summary.visiting.info.team_id) == (265, 386)
    assert (summary.home.stats.goals, summary.visiting.stats.goals) == (7, 2)
    assert (summary.home.stats.shots, summary.visiting.stats.shots) == (35, 24)
    assert summary.home.stats.power_play_opportunities == 0


def test_period_shots_sum_to_team_shots(summary):
    assert sum(p.stats.home_shots for p in summary.periods) == 35
    assert sum(p.stats.visiting_shots for p in summary.periods) == 24
    assert [p.period.number for p in summary.periods] == [1, 2, 3]


def test_first_goal(summary):
    goal = summary.goals[0]

    assert goal.goal_id == 218472
    assert goal.team.team_id == 265
    assert (goal.period.number, goal.seconds) == (1, 4 * 60 + 39)
    assert goal.scorer.player_id == 21780
    assert goal.assists[0].player_id == 43401
    assert not goal.properties.is_power_play


def test_goal_and_penalty_counts(summary):
    assert len(summary.goals) == 9
    assert len(summary.penalties) == 15
    assert sum(p.minutes for p in summary.penalties) == 111


def test_first_penalty(summary):
    penalty = summary.penalties[0]

    assert (penalty.period.number, penalty.seconds) == (1, 23)
    assert penalty.description == "Roughing"
    assert penalty.rule_number == "53.1"
    assert penalty.taken_by is not None and penalty.taken_by.player_id == 34276


def test_goalie_appearances_come_from_the_log_not_the_dressed_list(summary):
    """A backup (43404) dressed for Cincinnati but never played; only the starter appears."""
    uc = [(a.goalie.player_id, a.toi_seconds, a.stats.saves) for a in summary.home.goalie_log]
    assert uc == [(33425, 3600, 22)]


def test_bench_penalty_has_no_player():
    raw = copy.deepcopy(payload("game_summary"))
    penalty = raw["periods"][0]["penalties"][0]
    penalty.update(isBench=True, takenBy={"id": 0, "firstName": None, "lastName": None})

    parsed = parse_game_summary(raw).penalties[0]

    assert parsed.is_bench
    assert parsed.taken_by is None


def test_unknown_period_shape_fails_loudly():
    raw = copy.deepcopy(payload("game_summary"))
    raw["periods"][0]["goals"][0]["period"]["id"] = "SO"

    with pytest.raises(ValidationError, match="period"):
        parse_game_summary(raw)


def test_major_with_game_misconduct_and_coincidental_minor():
    """Game 32130, 3rd period at 12:44: a UC player (43406) gets a cross-checking major
    plus a game misconduct, and a home player (27981) a coincidental cross-checking minor."""
    penalties = parse_game_summary(payload("game_summary_32130")).penalties

    at_1244 = sorted(
        (p.taken_by.player_id, p.minutes, p.description)
        for p in penalties
        if p.taken_by is not None and (p.period.number, p.seconds) == (3, 764)
    )
    assert at_1244 == [
        (27981, 2, "Cross Checking"),
        (43406, 5, "Major-Cross Checking"),
        (43406, 10, "Game Misconduct"),
    ]
    assert len(penalties) == 11
    assert sum(p.minutes for p in penalties) == 46


def test_canary_is_quiet_on_real_data():
    assert canary_findings(payload("game_summary")) == []
    assert canary_findings(payload("game_summary_32130")) == []


def test_canary_reports_newly_recorded_skater_shots():
    raw = copy.deepcopy(payload("game_summary"))
    raw["homeTeam"]["skaters"][0]["stats"]["shots"] = 3
    raw["periods"][0]["goals"][0]["plus_players"] = [{"id": 1}]

    assert canary_findings(raw) == ["goal.on_ice_players", "homeTeam.skater.shots"]


@pytest.mark.parametrize(("code", "shootout"), [("0", False), ("1", True), ("2", True)])
def test_shootout_is_a_winner_code_not_a_flag(code, shootout):
    """Live 2026-27 data: 1 = home won the shootout, 2 = visitor won."""
    row = dict(payload("team_schedule")["SiteKit"]["Schedule"][0], shootout=code)
    [game] = parse_schedule({"SiteKit": {"Schedule": [row]}})
    assert game.shootout is shootout


def test_unknown_shootout_code_fails_loudly():
    row = dict(payload("team_schedule")["SiteKit"]["Schedule"][0], shootout="3")
    with pytest.raises(ValidationError, match="shootout"):
        parse_schedule({"SiteKit": {"Schedule": [row]}})
