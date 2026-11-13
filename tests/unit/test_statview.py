"""Parsers for the league's season player stats (compared against, never trusted)."""

from __future__ import annotations

from typing import Any

import pytest

from bca.ingest.parsers.statview import parse_goalie_stats, parse_skater_stats
from tests.fixture_data import fixture_json


def payload(name: str) -> Any:
    return fixture_json(f"live/{name}.json")


def test_goalie_lines_include_the_goalie_no_box_score_mentions():
    goalies = {g.player_id: g for g in parse_goalie_stats(payload("goalie_stats"))}

    assert len(goalies) == 5
    upstream_only = goalies[36407]
    assert (upstream_only.games_played, upstream_only.goals_against) == (2, 14)
    assert sum(g.goals_against for g in goalies.values()) == 76


def test_goalie_minutes_coerce_from_int_or_string():
    goalies = {g.player_id: g for g in parse_goalie_stats(payload("goalie_stats"))}
    assert goalies[33425].minutes_played == 339


def test_skater_lines_coerce_types():
    skaters = parse_skater_stats(payload("skater_stats"))

    assert len(skaters) == 29
    top = skaters[0]
    assert (top.player_id, top.goals, top.assists, top.penalty_minutes) == (33548, 7, 5, 4)
    assert any(s.penalty_minutes is None for s in skaters), "blank stays blank, not 0"


def test_unexpected_shape_fails_loudly():
    with pytest.raises(ValueError, match="sections"):
        parse_skater_stats({"SiteKit": {}})
