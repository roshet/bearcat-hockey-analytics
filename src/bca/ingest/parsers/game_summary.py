"""Parser for ``statviewfeed&view=gameSummary`` -- the full box score.

Two traps this module exists to get right:

- **Goalie appearances come from ``goalieLog``, not ``goalies``.** ``goalies``
  lists every dressed goalie, including a backup who never played (in game
  33169 it carries the backup with all zeros); ``goalieLog`` is who was actually in
  net, with in/out times.
- **Always-zero fields are not modelled as data.** Skater shots, hits, blocks,
  faceoffs, TOI, plus/minus and on-ice personnel are never recorded by the
  ACHA. They are read only by :func:`canary_findings`, which reports any
  non-zero value -- because that would mean the league has started recording
  real events, and that should interrupt whatever is in progress.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from bca.ingest.parsers.common import (
    FeedBool,
    FeedInt,
    FeedModel,
    FeedText,
    OptDateTime,
    OptFeedInt,
    clock_to_seconds,
    parse_period,
)

#: The feed's placeholder for "no player entered".
PLACEHOLDER_PLAYER_ID = 0


class Person(FeedModel):
    player_id: FeedInt = Field(alias="id")
    first_name: FeedText = Field(alias="firstName")
    last_name: FeedText = Field(alias="lastName")
    jersey_number: OptFeedInt = Field(default=None, alias="jerseyNumber")
    position: FeedText = ""


def _person_or_none(value: Any) -> Any:
    """Bench penalties and some edge cases carry an empty or id-0 person."""
    if not value or not isinstance(value, dict):
        return None
    if str(value.get("id") or "0").strip() in {"", "0"}:
        return None
    return value


class Period(FeedModel):
    number: int = Field(alias="id")

    @field_validator("number", mode="before")
    @classmethod
    def _period(cls, value: Any) -> int:
        return parse_period(value)


class TeamRef(FeedModel):
    team_id: FeedInt = Field(alias="id")


class GoalProperties(FeedModel):
    is_power_play: FeedBool = Field(alias="isPowerPlay")
    is_short_handed: FeedBool = Field(alias="isShortHanded")
    is_empty_net: FeedBool = Field(alias="isEmptyNet")
    is_penalty_shot: FeedBool = Field(default=False, alias="isPenaltyShot")
    is_game_winning: FeedBool = Field(default=False, alias="isGameWinningGoal")


class Goal(FeedModel):
    goal_id: FeedInt = Field(alias="game_goal_id")
    team: TeamRef
    period: Period
    #: None when the scorekeeper entered no time. Happens game-wide: in 20 of
    #: 379 games in 2026-27 every goal time is blank while every penalty time
    #: is present.
    seconds: int | None = Field(alias="time")
    #: None in a score-only box score, where the scorer is a placeholder id 0.
    scorer: Person | None = Field(alias="scoredBy")
    assists: list[Person]
    properties: GoalProperties

    _scorer = field_validator("scorer", mode="before")(_person_or_none)

    @field_validator("assists", mode="before")
    @classmethod
    def _real_assists(cls, value: Any) -> Any:
        return [a for a in value or [] if _person_or_none(a) is not None]

    _clock = field_validator("seconds", mode="before")(
        lambda v: None if v in (None, "") else clock_to_seconds(str(v))
    )


class Penalty(FeedModel):
    penalty_id: FeedInt = Field(alias="game_penalty_id")
    team: TeamRef = Field(alias="againstTeam")
    period: Period
    seconds: int = Field(alias="time")
    minutes: FeedInt
    description: FeedText
    rule_number: FeedText = Field(default="", alias="ruleNumber")
    taken_by: Person | None = Field(default=None, alias="takenBy")
    served_by: Person | None = Field(default=None, alias="servedBy")
    is_bench: FeedBool = Field(alias="isBench")
    #: The scorekeeper's power-play flag. A hint, not truth: game 32130 marks a
    #: Game Misconduct, which can never create a power play, as ``True``.
    upstream_is_power_play: FeedBool = Field(alias="isPowerPlay")

    _clock = field_validator("seconds", mode="before")(lambda v: clock_to_seconds(str(v)))
    _people = field_validator("taken_by", "served_by", mode="before")(_person_or_none)


class PeriodStats(FeedModel):
    home_goals: FeedInt = Field(alias="homeGoals")
    home_shots: FeedInt = Field(alias="homeShots")
    visiting_goals: FeedInt = Field(alias="visitingGoals")
    visiting_shots: FeedInt = Field(alias="visitingShots")


class GamePeriod(FeedModel):
    period: Period = Field(alias="info")
    stats: PeriodStats
    goals: list[Goal]
    penalties: list[Penalty]


class TeamBoxStats(FeedModel):
    shots: FeedInt
    goals: FeedInt
    power_play_goals: FeedInt = Field(alias="powerPlayGoals")
    #: As entered. ``0`` may mean "none" or "never entered"; see M2.
    power_play_opportunities: FeedInt = Field(alias="powerPlayOpportunities")
    penalty_minutes: FeedInt = Field(alias="penaltyMinuteCount")
    infractions: FeedInt = Field(alias="infractionCount")


class SkaterStats(FeedModel):
    goals: FeedInt
    assists: FeedInt
    points: FeedInt
    penalty_minutes: FeedInt = Field(alias="penaltyMinutes")


class SkaterLine(FeedModel):
    player: Person = Field(alias="info")
    stats: SkaterStats
    starting: FeedBool = False


class GoalieStats(FeedModel):
    time_on_ice: FeedText = Field(default="", alias="timeOnIce")
    shots_against: FeedInt = Field(alias="shotsAgainst")
    goals_against: FeedInt = Field(alias="goalsAgainst")
    saves: FeedInt


class GoalieAppearance(FeedModel):
    goalie: Person = Field(alias="info")
    stats: GoalieStats
    period_in: Period = Field(alias="periodStart")
    seconds_in: int = Field(alias="timeStart")
    period_out: Period = Field(alias="periodEnd")
    seconds_out: int = Field(alias="timeEnd")
    result: FeedText = ""

    _clock = field_validator("seconds_in", "seconds_out", mode="before")(
        lambda v: clock_to_seconds(str(v))
    )

    @property
    def toi_seconds(self) -> int | None:
        # "60:00" is minutes:seconds over the whole game, so it can exceed the
        # two-digit clock_to_seconds accepts.
        minutes, _, seconds = self.stats.time_on_ice.partition(":")
        if not (minutes.isdigit() and seconds.isdigit()):
            return None
        return int(minutes) * 60 + int(seconds)


class TeamInfo(FeedModel):
    team_id: FeedInt = Field(alias="id")
    name: FeedText
    abbreviation: FeedText = ""
    division_name: FeedText = Field(default="", alias="divisionName")


class TeamBlock(FeedModel):
    info: TeamInfo
    stats: TeamBoxStats
    skaters: list[SkaterLine]
    goalie_log: list[GoalieAppearance] = Field(alias="goalieLog")


class GameDetails(FeedModel):
    game_id: FeedInt = Field(alias="id")
    season_id: FeedInt = Field(alias="seasonId")
    venue: FeedText = ""
    attendance: OptFeedInt = None
    start_time: OptDateTime = Field(default=None, alias="GameDateISO8601")
    is_final: FeedBool = Field(alias="final")
    status: FeedText = ""


class GameSummary(FeedModel):
    details: GameDetails
    has_shootout: FeedBool = Field(alias="hasShootout")
    home: TeamBlock = Field(alias="homeTeam")
    visiting: TeamBlock = Field(alias="visitingTeam")
    periods: list[GamePeriod]

    @property
    def goals(self) -> list[Goal]:
        return [goal for period in self.periods for goal in period.goals]

    @property
    def penalties(self) -> list[Penalty]:
        return [penalty for period in self.periods for penalty in period.penalties]

    @property
    def is_score_only(self) -> bool:
        """No lineup entered: every skater line is the placeholder player.

        Seen in 20 of 379 games in 2026-27. Such a box score has the score and
        anonymous, untimed goals -- and no shots, penalties or goalies. Zeros in
        it mean "not entered", never "none happened".
        """
        lines = self.home.skaters + self.visiting.skaters
        return all(line.player.player_id == PLACEHOLDER_PLAYER_ID for line in lines)


def _summary_object(payload: Any) -> Any:
    """statviewfeed responses arrive as a one-element list once unwrapped."""
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError(f"expected one game summary, got {len(payload)}")
        payload = payload[0]
    if not isinstance(payload, dict) or "details" not in payload:
        raise ValueError("payload is not a game summary")
    return payload


def parse_game_summary(payload: Any) -> GameSummary:
    return GameSummary.model_validate(_summary_object(payload))


# --- canary ------------------------------------------------------------------

_SKATER_ZERO_FIELDS = (
    "shots",
    "hits",
    "blockedShots",
    "faceoffAttempts",
    "faceoffWins",
    "plusMinus",
)
_ZERO = (0, "0", None)
_TEAM_ZERO_FIELDS = ("hits", "faceoffAttempts", "faceoffWins")
_BLANK_TOI = {None, "", "0:00"}

#: The one known exception, found by the M1 backfill: for 2023-09-08..19 some
#: scorekeepers entered skater shots, plus/minus and on-ice "plus" players, then
#: stopped. The "minus" side was filled in for only 2 of these games, so the data
#: is partial and unusable. Only these games and these fields are expected; any
#: other finding, in any game, is still a canary.
SEPTEMBER_2023_TRIAL_GAMES = frozenset({
    12493, 12534, 12535, 12565, 12566, 12577, 12578, 12595, 12596, 12656, 12700,
    13058, 13457, 13481, 13808, 13876, 13966, 14134, 14135, 14296, 14529, 14530,
    14947, 14973, 15197, 15240, 15664,
})
SEPTEMBER_2023_TRIAL_FIELDS = frozenset({
    "homeTeam.skater.shots",
    "visitingTeam.skater.shots",
    "homeTeam.skater.plusMinus",
    "visitingTeam.skater.plusMinus",
    "goal.on_ice_players",
})


def is_known_trial_finding(game_id: int, finding: str) -> bool:
    return game_id in SEPTEMBER_2023_TRIAL_GAMES and finding in SEPTEMBER_2023_TRIAL_FIELDS


def canary_findings(payload: Any) -> list[str]:
    """Every field the ACHA has never recorded that is non-zero in this game.

    An empty list is the expected, boring result. Anything else means the
    feed's coverage has changed and the roadmap may have opened up.
    """
    summary = _summary_object(payload)
    found: list[str] = []

    for side in ("homeTeam", "visitingTeam"):
        team = summary.get(side) or {}
        stats = team.get("stats") or {}
        found += [f"{side}.stats.{f}" for f in _TEAM_ZERO_FIELDS if stats.get(f) not in _ZERO]

        for skater in team.get("skaters") or []:
            s = skater.get("stats") or {}
            found += [
                f"{side}.skater.{f}" for f in _SKATER_ZERO_FIELDS if s.get(f) not in _ZERO
            ]
            if s.get("toi") not in _BLANK_TOI:
                found.append(f"{side}.skater.toi")

    for period in summary.get("periods") or []:
        for goal in period.get("goals") or []:
            if goal.get("plus_players") or goal.get("minus_players"):
                found.append("goal.on_ice_players")

    return sorted(set(found))
