"""Pin the assumptions about ACHA data that the product design rests on.

Every feature decision in this project follows from what the feed does and does
not contain. Those findings are recorded in the research report, but a document
cannot fail a build. These tests can.

They run against payloads archived from the live feed (``bca feed check
--out-dir tests/fixtures/live``). Two kinds of failure are both informative:

- A *negative* assertion breaking is good news. If individual skater shots stop
  being zero, the ACHA has started recording them and real shot-based metrics
  become possible. That should interrupt whatever we were doing.
- A *positive* assertion breaking means a data source we rely on has gone away.

Fixtures are from University of Cincinnati (team 265), season 73 (2026-27), and
game 33169 (Cincinnati 7-2 Kent State, 2026-09-18).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "live"
UC_TEAM_ID = "265"

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists(),
    reason="no archived payloads; run `bca feed check --out-dir tests/fixtures/live`",
)


def payload(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --- what we can rely on ---------------------------------------------------


def test_schedule_carries_final_scores_for_completed_games():
    """Game rows are our source of truth for every record and aggregate."""
    games = payload("team_schedule")["SiteKit"]["Schedule"]
    final = [g for g in games if g["final"] == "1"]

    assert len(final) >= 10
    for game in final:
        assert game["home_goal_count"].isdigit()
        assert game["visiting_goal_count"].isdigit()
        assert game["date_played"]
        assert game["venue_name"]


def test_uc_record_is_computable_from_game_rows_alone():
    """The whole aggregation strategy in one test.

    Values as of the 2026-10-21 archive: 2-8, 25 goals for, 63 against through
    10 games. Later archives will move these numbers; the point is that they are
    derivable without trusting any upstream summary.
    """
    games = payload("team_schedule")["SiteKit"]["Schedule"]
    wins = losses = goals_for = goals_against = 0

    for game in games:
        if game["final"] != "1":
            continue
        at_home = game["home_team"] == UC_TEAM_ID
        scored = int(game["home_goal_count"] if at_home else game["visiting_goal_count"])
        allowed = int(game["visiting_goal_count"] if at_home else game["home_goal_count"])
        goals_for += scored
        goals_against += allowed
        wins += scored > allowed
        losses += scored < allowed

    assert (wins, losses) == (2, 8)
    assert (goals_for, goals_against) == (25, 63)


def test_team_shots_are_recorded_by_period():
    """Period-level shot share is the one tracked possession proxy we have."""
    periods = payload("game_summary")["periods"]
    assert len(periods) == 3

    for period in periods:
        assert int(period["stats"]["homeShots"]) > 0
        assert int(period["stats"]["visitingShots"]) > 0


def test_numeric_fields_are_inconsistently_typed():
    """The feed mixes JSON strings and numbers for the same kind of value.

    Team-level ``shots`` arrives as an integer while period-level ``homeShots``
    arrives as a string, and roster numbers are strings throughout. Every parser
    must coerce rather than trust, so this is pinned deliberately.
    """
    summary = payload("game_summary")

    assert isinstance(summary["homeTeam"]["stats"]["shots"], int)
    assert isinstance(summary["periods"][0]["stats"]["homeShots"], str)


def test_goal_events_carry_time_scorer_and_strength():
    goals = [g for p in payload("game_summary")["periods"] for g in p["goals"]]
    assert len(goals) >= 8

    for goal in goals:
        assert goal["time"]
        assert goal["scoredBy"]["id"]
        assert set(goal["properties"]) >= {"isPowerPlay", "isShortHanded", "isEmptyNet"}


def test_penalty_events_carry_everything_the_simulator_needs():
    """The power-play state simulator depends entirely on these five fields."""
    penalties = [p for period in payload("game_summary")["periods"] for p in period["penalties"]]
    assert len(penalties) == 15

    for penalty in penalties:
        assert penalty["time"]
        assert penalty["period"]["id"]
        assert penalty["minutes"] > 0
        assert penalty["description"]
        assert penalty["againstTeam"]["id"]


def test_goalie_appearances_have_real_shot_and_save_counts():
    """Goaltending is the one position where the feed gives us genuine volume."""
    teams = [payload("game_summary")["homeTeam"], payload("game_summary")["visitingTeam"]]
    goalies = [g for team in teams for g in team["goalies"]]
    assert goalies

    assert any(g["stats"]["shotsAgainst"] > 0 and g["stats"]["saves"] > 0 for g in goalies)


def test_roster_mixes_players_and_a_nested_coach_list():
    """The roster response is heterogeneous and must be split by type.

    ``SiteKit.Roster`` is a list of player objects with a *nested list* of coach
    objects appended as the final element. Iterating it as uniform records
    crashes, so the parser has to partition on type.
    """
    roster = payload("team_roster")["SiteKit"]["Roster"]

    players = [entry for entry in roster if isinstance(entry, dict)]
    coach_groups = [entry for entry in roster if isinstance(entry, list)]

    assert len(players) >= 20
    assert len(coach_groups) == 1

    coaches = coach_groups[0]
    assert any(c["role"] == "Head Coach" for c in coaches)
    assert all(c.get("first_name") and c.get("last_name") for c in coaches)


# --- what is missing, and must stay accounted for --------------------------


@pytest.mark.parametrize(
    "field",
    ["shots", "faceoffAttempts", "hits", "blockedShots", "plusMinus"],
)
def test_individual_skater_tracking_stats_are_not_recorded(field: str):
    """Why there is no Corsi, no shooting percentage and no player comparison.

    If this starts failing, the ACHA has begun recording real skater events and
    a large part of the roadmap just became possible.
    """
    summary = payload("game_summary")
    skaters = summary["homeTeam"]["skaters"] + summary["visitingTeam"]["skaters"]
    assert skaters

    assert {s["stats"][field] for s in skaters} == {0}, (
        f"{field} is now populated -- advanced metrics may be unlocked"
    )


def test_skater_time_on_ice_is_not_recorded():
    """No TOI means no per-60 rates and no deployment analysis for skaters."""
    summary = payload("game_summary")
    skaters = summary["homeTeam"]["skaters"] + summary["visitingTeam"]["skaters"]

    assert {s["stats"]["toi"] for s in skaters} == {"0:00"}


def test_on_ice_players_for_goals_are_not_recorded():
    """No on-ice personnel means no line-combination analysis from the feed."""
    goals = [g for p in payload("game_summary")["periods"] for g in p["goals"]]

    for goal in goals:
        assert goal["plus_players"] == []
        assert goal["minus_players"] == []


def test_play_by_play_contains_no_shot_or_faceoff_events():
    events = payload("play_by_play")["GC"]["Pxpverbose"]
    assert {e["event"] for e in events} == {"goal", "penalty", "goalie_change"}


def test_play_by_play_coordinates_are_present_but_always_zero():
    """The fields exist, which is a trap. Nothing may read them as locations."""
    events = payload("play_by_play")["GC"]["Pxpverbose"]
    located = [e for e in events if "x_location" in e]
    assert located

    for event in located:
        assert int(event["x_location"]) == 0
        assert int(event["y_location"]) == 0


def test_power_play_opportunities_are_sometimes_left_unentered():
    """The field is real but not uniformly complete.

    Measured across Cincinnati's ten completed 2026-27 games, 8 of 10 are
    populated; only the opening weekend is missing. Game 33169 is that opening
    weekend, and this fixture pins it: 15 penalties and 111 combined penalty
    minutes, reported as zero opportunities for both teams, while still crediting
    Cincinnati with 2 power-play goals.

    Because completeness varies per game, a cross-team or cross-season series
    built on this field would be silently uneven -- which is the reason the
    project derives power-play state from the penalty log. Power-play *time* is
    never published at all.
    """
    summary = payload("game_summary")
    home = summary["homeTeam"]["stats"]
    away = summary["visitingTeam"]["stats"]

    assert home["powerPlayGoals"] == 2, "power-play goals were recorded"
    assert home["powerPlayOpportunities"] == 0, "but opportunities were not"
    assert away["powerPlayOpportunities"] == 0
    assert home["penaltyMinuteCount"] + away["penaltyMinuteCount"] == 111


def test_officials_and_scorekeepers_are_not_recorded():
    """These keys exist and look useful. They are empty.

    An early draft of the research listed referees, linesmen and scorekeepers as
    populated game metadata. They are not, so no feature may depend on knowing
    who officiated or scored a game -- including any attempt to attribute data
    quality to a named scorekeeper.
    """
    summary = payload("game_summary")

    assert summary["referees"] == []
    assert summary["linesmen"] == []
    assert summary["scorekeepers"] == []


def test_faceoffs_are_not_recorded_at_team_level_either():
    summary = payload("game_summary")
    for side in ("homeTeam", "visitingTeam"):
        assert summary[side]["stats"]["faceoffAttempts"] == 0
        assert summary[side]["stats"]["faceoffWins"] == 0


def test_skater_stats_feed_has_no_plus_minus_column():
    """Season skater stats omit plus/minus entirely, not merely zero it."""
    rows = payload("skater_stats")[0]["sections"][0]["data"]
    assert rows

    columns = set(rows[0]["row"])
    assert "plus_minus" not in columns
    assert "plusminus" not in columns
    assert {"goals", "assists", "points", "penalty_minutes", "games_played"} <= columns


def test_shooting_percentage_is_structurally_meaningless():
    """It is published, and it is always zero, because shots are not counted."""
    rows = payload("skater_stats")[0]["sections"][0]["data"]
    assert {float(r["row"]["shooting_percentage"]) for r in rows} == {0.0}


def test_players_appear_in_season_stats_who_are_not_on_the_roster_feed():
    """A real integrity gap the reconciliation suite has to handle.

    Roster membership cannot be assumed to cover everyone who appears in stats,
    so player records must be created from whichever source sees them first.
    """
    roster_ids = {
        entry["player_id"]
        for entry in payload("team_roster")["SiteKit"]["Roster"]
        if isinstance(entry, dict) and entry.get("player_id")
    }
    stat_ids = {r["row"]["player_id"] for r in payload("skater_stats")[0]["sections"][0]["data"]}

    assert stat_ids - roster_ids, "expected at least one off-roster player in season stats"
