"""Parsers for ``statviewfeed`` season player statistics (``view=players``).

These are the league's own aggregates. Convention 1 says they are never a
source: they are loaded only so the audit can show where they disagree with
the game rows (UC's 2026-27 goalie lines sum to 76 goals against; the games,
to 63).

Shape: ``[{"sections": [{"headers": {...}, "data": [{"prop": ..., "row": ...}]}]}]``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from bca.ingest.parsers.common import FeedInt, FeedModel, FeedText, OptFeedInt


class _SeasonLine(FeedModel):
    """Counts are optional: the feed leaves some blank (``""``), and the audit,
    not the parser, decides what a blank means."""

    player_id: FeedInt
    name: FeedText
    #: The division the league files this line under. When it is not the
    #: team's own, the line also counts games for the school's other squad
    #: (seen: a Jamestown MD1 skater's 17 GP = 10 MD1 games + 7 for its MD2 side).
    division: FeedText = ""
    games_played: OptFeedInt = None


class SkaterSeasonLine(_SeasonLine):
    position: FeedText = ""
    goals: OptFeedInt = None
    assists: OptFeedInt = None
    points: OptFeedInt = None
    penalty_minutes: OptFeedInt = None
    shootout_winning_goals: OptFeedInt = None


class GoalieSeasonLine(_SeasonLine):
    goals_against: OptFeedInt = None
    saves: OptFeedInt = None
    #: Whole minutes; arrives as an int or a string.
    minutes_played: OptFeedInt = Field(default=None)
    shootout_losses: OptFeedInt = None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError(f"expected one stats block, got {len(payload)}")
        payload = payload[0]
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        raise ValueError("player stats payload has no sections")
    sections = payload["sections"]
    if len(sections) != 1:
        raise ValueError(f"expected one stats section, got {len(sections)}")
    return [item["row"] for item in sections[0].get("data") or []]


def parse_skater_stats(payload: Any) -> list[SkaterSeasonLine]:
    return [SkaterSeasonLine.model_validate(row) for row in _rows(payload)]


def parse_goalie_stats(payload: Any) -> list[GoalieSeasonLine]:
    return [GoalieSeasonLine.model_validate(row) for row in _rows(payload)]
