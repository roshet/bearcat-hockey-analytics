"""Named constructors for the ACHA feed endpoints we have verified by hand.

Everything in this module was confirmed to return live ACHA data on 2026-10-21.
Keeping the parameter sets here -- rather than letting callers assemble query
strings -- means the knowledge gained from probing an undocumented API lives in
one reviewable place, and a parameter that turns out to be wrong is fixed once.

Endpoints deliberately absent because they were probed and do NOT work:

- ``statviewfeed&view=standings``   -> ``{"error": "InvalidView error: standings"}``
- ``statviewfeed&view=teams``       -> HTTP 200 with an empty payload
- ``modulekit&view=standingtype``   -> returns request metadata only, no rows

Standings are therefore computed from game rows instead, which is what we would
want regardless: the aggregate is then ours, deterministic, and testable.
"""

from __future__ import annotations

from typing import Final

from bca.ingest.client import FeedRequest

MODULEKIT: Final = "modulekit"
STATVIEWFEED: Final = "statviewfeed"
GAMECENTER: Final = "gc"

#: ``season_id`` for every ACHA men's regular season, oldest first.
#: Tournament and women's seasons are separate ids and intentionally excluded.
MENS_REGULAR_SEASONS: Final[dict[str, int]] = {
    "2020-21": 1,
    "2021-22": 10,
    "2022-23": 21,
    "2023-24": 34,
    "2024-25": 46,
    "2025-26": 60,
    "2026-27": 73,
}

#: The current men's season. Update once a year, or derive from ``seasons()``.
CURRENT_MENS_SEASON_ID: Final = 73

#: University of Cincinnati. Stable across the program's MD2 -> MD1 move.
UC_TEAM_ID: Final = 265


def seasons() -> FeedRequest:
    """Every season the league has ever registered, with ids and date ranges."""
    return FeedRequest(MODULEKIT, {"view": "seasons"})


def teams_by_season(season_id: int) -> FeedRequest:
    """All teams in a season, with division id / name and logo URL."""
    return FeedRequest(MODULEKIT, {"view": "teamsbyseason", "season_id": str(season_id)})


def team_schedule(team_id: int, season_id: int) -> FeedRequest:
    """One team's full schedule, including final scores for completed games.

    This is our source of truth for results. Records, goals for/against and
    standings are all computed from these rows rather than read from any
    upstream aggregate.
    """
    return FeedRequest(
        MODULEKIT,
        {"view": "schedule", "team_id": str(team_id), "season_id": str(season_id)},
    )


def team_roster(team_id: int, season_id: int) -> FeedRequest:
    """One team's roster for a season, plus its coaching staff."""
    return FeedRequest(
        MODULEKIT,
        {"view": "roster", "team_id": str(team_id), "season_id": str(season_id)},
    )


def game_summary(game_id: int) -> FeedRequest:
    """Full box score: the richest single resource the feed offers.

    Contains team shots by period, the complete goal and penalty event logs with
    timestamps, and goalie appearances. The penalty log is what the power-play
    state simulator is built on.
    """
    return FeedRequest(STATVIEWFEED, {"view": "gameSummary", "game_id": str(game_id)})


def play_by_play(game_id: int) -> FeedRequest:
    """Verbose play-by-play.

    Only ``goal``, ``penalty`` and ``goalie_change`` events exist in ACHA data;
    there are no shot, faceoff or zone events, and the ``x_location`` /
    ``y_location`` fields are present but always zero. Kept because the goalie
    change timings are easier to read here than in the box score.
    """
    return FeedRequest(GAMECENTER, {"tab": "pxpverbose", "game_id": str(game_id)})


def skater_stats(season_id: int, team_id: int | None = None, limit: int = 100) -> FeedRequest:
    """Season skater statistics, optionally narrowed to one team.

    Note the absent columns: there is no plus/minus, and ``shooting_percentage``
    is always zero because individual shots are not recorded.
    """
    return _player_stats("skaters", season_id, team_id, limit, sort="points")


def goalie_stats(season_id: int, team_id: int | None = None, limit: int = 100) -> FeedRequest:
    """Season goalie statistics. Shots against, saves and minutes are real here."""
    return _player_stats("goalies", season_id, team_id, limit, sort="save_percentage")


def player_game_log(player_id: int, season_id: int) -> FeedRequest:
    """One player's game-by-game log for a season."""
    return FeedRequest(
        MODULEKIT,
        {
            "view": "player",
            "category": "gamebygame",
            "player_id": str(player_id),
            "season_id": str(season_id),
        },
    )


def _player_stats(
    position: str,
    season_id: int,
    team_id: int | None,
    limit: int,
    *,
    sort: str,
) -> FeedRequest:
    params = {
        "view": "players",
        # ``season``, not ``season_id``: this view silently ignores ``season_id``
        # and answers with the current season, whatever was asked for. Found by
        # the M2 audit, when every season's archived lines came back identical.
        "season": str(season_id),
        "position": position,
        "rookies": "0",
        "statsType": "standard",
        "site_id": "0",
        "first": "0",
        "limit": str(limit),
        "sort": sort,
        "order_direction": "desc",
        "division": "-1",
        "conference": "-1",
        "qualified": "all",
        "lang": "en",
    }
    if team_id is not None:
        params["team_id"] = str(team_id)
    return FeedRequest(STATVIEWFEED, params)
