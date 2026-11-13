"""Parsers for the ``modulekit`` views: seasons, teams, schedules and rosters.

All four wrap their rows in ``{"SiteKit": {"<View>": [...]}}``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from bca.ingest.parsers.common import (
    FeedBool,
    FeedInt,
    FeedModel,
    FeedText,
    OptDate,
    OptDateTime,
    OptFeedInt,
)

#: Prefix on ``division_long_name`` for Men's Division 1 conferences, 2023-24 on.
MD1_DIVISION_PREFIX = "MD1"

#: What Men's Division 1 was called before conferences appeared in the feed.
#: Seasons 1, 10 and 21 (2020-21 to 2022-23) have no "MD1 ..." divisions at all,
#: only "Men's Division 2", "Men's Division 3" and "Men's Pool A". Pool A is D1:
#: every team in it is named "M1 ...", and 26/27, 66/67 and 69/69 of its members
#: are MD1 in 2023-24. Found by `bca ingest backfill --dry-run` on 2026-10-22.
MD1_LEGACY_DIVISIONS = frozenset({"Men's Pool A"})


def _rows(payload: Any, view: str) -> list[Any]:
    try:
        rows = payload["SiteKit"][view]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"payload has no SiteKit.{view}") from exc
    if not isinstance(rows, list):
        raise ValueError(f"SiteKit.{view} is {type(rows).__name__}, expected a list")
    return rows


# --- seasons ---------------------------------------------------------------


class Season(FeedModel):
    season_id: FeedInt
    name: FeedText = Field(alias="season_name")
    short_name: FeedText = Field(alias="shortname")
    is_playoff: FeedBool = Field(alias="playoff")
    start_date: OptDate
    end_date: OptDate


def parse_seasons(payload: Any) -> list[Season]:
    return [Season.model_validate(row) for row in _rows(payload, "Seasons")]


# --- teams by season -------------------------------------------------------


class TeamSeason(FeedModel):
    team_id: FeedInt = Field(alias="id")
    name: FeedText
    nickname: FeedText
    code: FeedText
    division_id: OptFeedInt
    division_name: FeedText = Field(alias="division_long_name")

    @property
    def is_md1(self) -> bool:
        return (
            self.division_name.startswith(MD1_DIVISION_PREFIX)
            or self.division_name in MD1_LEGACY_DIVISIONS
        )


def parse_teams_by_season(payload: Any) -> list[TeamSeason]:
    return [TeamSeason.model_validate(row) for row in _rows(payload, "Teamsbyseason")]


# --- schedule ----------------------------------------------------------------


class ScheduledGame(FeedModel):
    game_id: FeedInt
    season_id: FeedInt
    date_played: OptDate
    start_time: OptDateTime = Field(alias="GameDateISO8601")
    home_team_id: FeedInt = Field(alias="home_team")
    visiting_team_id: FeedInt = Field(alias="visiting_team")
    home_goals: FeedInt = Field(alias="home_goal_count")
    visiting_goals: FeedInt = Field(alias="visiting_goal_count")
    is_final: FeedBool = Field(alias="final")
    overtime: FeedBool
    #: True for a game decided by shootout. See ``_shootout_code``.
    shootout: bool
    status: FeedText = Field(alias="game_status")
    venue: FeedText = Field(alias="venue_name")
    attendance: OptFeedInt

    @field_validator("shootout", mode="before")
    @classmethod
    def _shootout_code(cls, value: Any) -> bool:
        """``shootout`` is a code, not a flag: 0 none, 1 home won, 2 visitor won.

        Every non-zero row has status "Final SO", and across the 16 shootouts in
        2026-27 the code matched the side with the extra goal every time
        (1 = home 7/7, 2 = visitor 9/9). The winner is already in the final
        score, so only "was there a shootout" is kept. Anything else raises.
        """
        code = str(value).strip()
        if code not in {"0", "1", "2"}:
            raise ValueError(f"unrecognised shootout code {value!r}")
        return code != "0"


def parse_schedule(payload: Any) -> list[ScheduledGame]:
    return [ScheduledGame.model_validate(row) for row in _rows(payload, "Schedule")]


# --- roster ------------------------------------------------------------------


class RosterPlayer(FeedModel):
    """A rostered player. Birthdates are deliberately not modelled."""

    player_id: FeedInt
    first_name: FeedText
    last_name: FeedText
    position: FeedText
    jersey_number: OptFeedInt = Field(alias="tp_jersey_number")
    shoots: FeedText = ""
    catches: FeedText = ""
    active: FeedBool = True


class StaffMember(FeedModel):
    person_id: FeedInt
    first_name: FeedText
    last_name: FeedText
    role: FeedText


class Roster(FeedModel):
    players: list[RosterPlayer]
    staff: list[StaffMember]


def parse_roster(payload: Any) -> Roster:
    """Split ``SiteKit.Roster``: player dicts, then a *nested list* of coaches."""
    players: list[RosterPlayer] = []
    staff: list[StaffMember] = []
    for entry in _rows(payload, "Roster"):
        if isinstance(entry, dict):
            players.append(RosterPlayer.model_validate(entry))
        elif isinstance(entry, list):
            staff.extend(StaffMember.model_validate(member) for member in entry)
        else:
            raise ValueError(f"unexpected roster entry of type {type(entry).__name__}")
    return Roster(players=players, staff=staff)
