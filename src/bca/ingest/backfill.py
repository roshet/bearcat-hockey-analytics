"""Walk the feed and archive every MD1 resource for the requested seasons.

This module only *fetches*. Nothing here writes to the database; that is
``normalize``'s job, and it runs offline from the archive this fills.

Per season: ``teamsbyseason`` -> MD1 teams -> each team's schedule and roster ->
the union of completed game ids -> each game's box score. Because the archive
answers repeat requests for free, an interrupted backfill resumes simply by
running it again.

Politeness is inherited, not re-implemented: every request goes through
``HockeyTechClient`` (one at a time, 1 s apart, identified). There is
deliberately no concurrency here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx

from bca.ingest import endpoints
from bca.ingest.client import FeedRequest, FeedResponse, HockeyTechClient
from bca.ingest.errors import FeedAuthError, IngestError
from bca.ingest.parsers import modulekit as mk

logger = logging.getLogger(__name__)

#: Called once per resource: (stage, request, was_served_from_archive).
ProgressHook = Callable[[str, FeedRequest, bool], None]
#: Called when a stage starts: (stage, number of resources in it, number uncached).
StageHook = Callable[[str, int, int], None]


@dataclass
class SeasonSurvey:
    """What one season holds, from a single ``teamsbyseason`` request."""

    season_id: int
    total_teams: int
    md1_teams: list[mk.TeamSeason]
    md1_divisions: list[str]


@dataclass
class SeasonResult:
    season_id: int
    md1_teams: int = 0
    final_games: int = 0
    fetched: int = 0
    from_archive: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


class Backfill:
    def __init__(
        self,
        client: HockeyTechClient,
        *,
        on_stage: StageHook | None = None,
        on_resource: ProgressHook | None = None,
    ) -> None:
        self.client = client
        self._on_stage = on_stage or (lambda *_: None)
        self._on_resource = on_resource or (lambda *_: None)

    # -- dry run -----------------------------------------------------------------

    def survey(self, season_ids: Iterable[int]) -> list[SeasonSurvey]:
        """Fetch only ``teamsbyseason`` per season (one request each).

        Used by ``--dry-run`` to confirm the MD1 rule holds in every season
        before the thousands of requests that depend on it are sent.
        """
        surveys = []
        for season_id in season_ids:
            response = self.client.fetch(endpoints.teams_by_season(season_id))
            teams = mk.parse_teams_by_season(response.payload)
            md1 = [t for t in teams if t.is_md1]
            surveys.append(
                SeasonSurvey(
                    season_id=season_id,
                    total_teams=len(teams),
                    md1_teams=md1,
                    md1_divisions=sorted({t.division_name for t in md1}),
                )
            )
        return surveys

    def uncached_team_requests(self, survey: SeasonSurvey) -> int:
        """Requests the schedule + roster stage would send for this season."""
        return sum(
            not self.client.is_cached(request)
            for team in survey.md1_teams
            for request in self._team_requests(team.team_id, survey.season_id)
        )

    # -- the real thing ----------------------------------------------------------

    def run(
        self,
        season_ids: Iterable[int],
        *,
        refresh_current: bool = False,
        upstream_stats: bool = False,
    ) -> list[SeasonResult]:
        """Archive every MD1 resource for each season, oldest first.

        ``upstream_stats`` also archives each team's season skater and goalie
        stats. Those are the league's own aggregates: kept only so the audit can
        compare them against the game rows, never used as a source.
        """
        self._fetch_one("seasons", endpoints.seasons(), SeasonResult(season_id=0))
        return [
            self._run_season(
                season_id, refresh_current=refresh_current, upstream_stats=upstream_stats
            )
            for season_id in sorted(season_ids)
        ]

    def _run_season(
        self, season_id: int, *, refresh_current: bool, upstream_stats: bool
    ) -> SeasonResult:
        result = SeasonResult(season_id=season_id)
        refresh = refresh_current and season_id == endpoints.CURRENT_MENS_SEASON_ID

        teams_response = self._fetch_one(
            "teams", endpoints.teams_by_season(season_id), result, refresh=refresh
        )
        if teams_response is None:
            return result
        md1 = [t for t in mk.parse_teams_by_season(teams_response.payload) if t.is_md1]
        result.md1_teams = len(md1)

        team_requests = [
            request
            for team in md1
            for request in self._team_requests(team.team_id, season_id, upstream_stats)
        ]
        self._stage(f"{season_id} team resources", team_requests, refresh)

        final_game_ids: set[int] = set()
        for request in team_requests:
            response = self._fetch_one("teams", request, result, refresh=refresh)
            if response is None or request.view != "schedule":
                continue
            try:
                games = mk.parse_schedule(response.payload)
            except ValueError as exc:  # includes pydantic.ValidationError
                result.failures.append((request.identity, f"unparseable schedule: {exc}"))
                continue
            final_game_ids.update(g.game_id for g in games if g.is_final)

        result.final_games = len(final_game_ids)
        summary_requests = [endpoints.game_summary(gid) for gid in sorted(final_game_ids)]
        self._stage(f"{season_id} box scores", summary_requests, False)
        for request in summary_requests:
            self._fetch_one("games", request, result)

        logger.info(
            "season %d: %d MD1 teams, %d final games, %d fetched, %d from archive, %d failed",
            season_id,
            result.md1_teams,
            result.final_games,
            result.fetched,
            result.from_archive,
            len(result.failures),
        )
        return result

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def _team_requests(
        team_id: int, season_id: int, upstream_stats: bool = False
    ) -> list[FeedRequest]:
        requests = [
            endpoints.team_schedule(team_id, season_id),
            endpoints.team_roster(team_id, season_id),
        ]
        if upstream_stats:
            requests += [
                endpoints.skater_stats(season_id, team_id),
                endpoints.goalie_stats(season_id, team_id),
            ]
        return requests

    def _stage(self, name: str, requests: list[FeedRequest], refresh: bool) -> None:
        uncached = len(requests) if refresh else sum(
            not self.client.is_cached(r) for r in requests
        )
        self._on_stage(name, len(requests), uncached)

    def _fetch_one(
        self,
        stage: str,
        request: FeedRequest,
        result: SeasonResult,
        *,
        refresh: bool = False,
    ) -> FeedResponse | None:
        """Fetch one resource. A single failure is recorded, never fatal --
        except a rejected key, which would fail every request after it."""
        try:
            response = self.client.fetch(request, refresh=refresh)
        except FeedAuthError:
            raise
        except (IngestError, httpx.HTTPStatusError) as exc:
            logger.warning("failed %s: %s", request.identity, exc)
            result.failures.append((request.identity, str(exc)))
            self._on_resource(stage, request, False)
            return None

        if response.from_cache:
            result.from_archive += 1
        else:
            result.fetched += 1
        self._on_resource(stage, request, response.from_cache)
        return response
