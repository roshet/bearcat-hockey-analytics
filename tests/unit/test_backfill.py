"""Backfill walker and coverage report, against a mock feed built from fixtures."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from bca.config import Settings
from bca.db.migrate import upgrade
from bca.db.session import make_engine, make_session_factory
from bca.ingest.archive import RawArchive
from bca.ingest.backfill import Backfill
from bca.ingest.client import HockeyTechClient
from bca.ingest.coverage import build_report
from bca.ingest.errors import FeedAuthError
from bca.ingest.normalize import Normalizer
from tests.fixture_data import fixture_json

SECHL = {52, 408, 265, 346, 347, 356}


def payload(name: str) -> Any:
    return fixture_json(f"live/{name}.json")


def small_league() -> Any:
    """teamsbyseason trimmed to the six SECHL teams plus two non-MD1 teams."""
    data = payload("teams_by_season")
    rows = data["SiteKit"]["Teamsbyseason"]
    md1 = [r for r in rows if int(r["id"]) in SECHL]
    others = [r for r in rows if not r["division_long_name"].startswith("MD1")][:2]
    data["SiteKit"]["Teamsbyseason"] = md1 + others
    return data


class FakeFeed:
    """Serves fixture payloads by view and records every request it sees."""

    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []
        self.fail: dict[tuple[str, str], int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.seen.append(params)
        view = params.get("view", "")
        status = self.fail.get((view, params.get("team_id", "")))
        if status:
            return httpx.Response(status, text="nope")
        body = {
            "seasons": lambda: payload("seasons"),
            "teamsbyseason": small_league,
            "schedule": lambda: payload("team_schedule"),
            "roster": lambda: payload("team_roster"),
            "gameSummary": lambda: [payload("game_summary")],
        }[view]()
        return httpx.Response(200, text=json.dumps(body))

    def views(self) -> Counter[str]:
        return Counter(p.get("view", "") for p in self.seen)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'bca.db').as_posix()}",
        cache_dir=tmp_path / "cache",
        request_delay_seconds=0.0,
        max_retries=0,
        contact_email="maintainer@bca.test",
        acha_api_key="test-key",
    )


def client_for(settings: Settings, feed: FakeFeed) -> HockeyTechClient:
    return HockeyTechClient(settings, transport=httpx.MockTransport(feed))


def test_walks_only_md1_teams_and_completed_games(settings):
    feed = FakeFeed()
    [result] = Backfill(client_for(settings, feed)).run([73])

    assert feed.views() == {
        "seasons": 1,
        "teamsbyseason": 1,
        "schedule": 6,
        "roster": 6,
        "gameSummary": 10,  # union of completed games, deduped across schedules
    }
    requested_teams = {int(p["team_id"]) for p in feed.seen if "team_id" in p}
    assert requested_teams == SECHL
    assert (result.md1_teams, result.final_games, result.failures) == (6, 10, [])


def test_rerun_is_served_entirely_from_the_archive(settings):
    Backfill(client_for(settings, FakeFeed())).run([73])

    again = FakeFeed()
    [result] = Backfill(client_for(settings, again)).run([73])

    assert again.seen == [], "resuming must not re-request anything"
    assert result.fetched == 0


def test_refresh_current_refetches_team_resources_but_not_box_scores(settings):
    Backfill(client_for(settings, FakeFeed())).run([73])

    again = FakeFeed()
    Backfill(client_for(settings, again)).run([73], refresh_current=True)

    assert again.views() == {"teamsbyseason": 1, "schedule": 6, "roster": 6}


def test_one_failing_resource_does_not_stop_the_season(settings):
    feed = FakeFeed()
    feed.fail[("roster", "346")] = 404

    [result] = Backfill(client_for(settings, feed)).run([73])

    assert len(result.failures) == 1
    assert "roster" in result.failures[0][0]
    assert feed.views()["gameSummary"] == 10


def test_rejected_key_aborts_immediately(settings):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Invalid key.")

    client = HockeyTechClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(FeedAuthError):
        Backfill(client).run([73])


def test_survey_reports_md1_divisions_with_one_request(settings):
    feed = FakeFeed()
    backfill = Backfill(client_for(settings, feed))

    [survey] = backfill.survey([73])

    assert feed.views() == {"teamsbyseason": 1}
    assert survey.total_teams == 8
    assert survey.md1_divisions == ["MD1 SECHL"]
    assert backfill.uncached_team_requests(survey) == 12


# --- coverage ------------------------------------------------------------------


@pytest.fixture
def sessions(settings) -> sessionmaker[Session]:
    upgrade(settings.database_url)
    return make_session_factory(make_engine(settings.database_url))


def test_coverage_flags_box_scores_that_describe_the_wrong_game(settings, sessions):
    # The fake serves 33169's box score for every game id. Only 33169's own
    # archive entry may load; the other nine describe the wrong game and must be
    # reported, not silently folded into 33169.
    Backfill(client_for(settings, FakeFeed())).run([73])
    archive = RawArchive(settings.resolved_cache_dir)
    Normalizer(sessions, archive).run()

    with sessions() as session:
        report = build_report(session, archive)
    season = next(s for s in report.seasons if s.season_id == 73)

    assert season.md1_teams == 6
    assert season.games_final == 10
    assert season.summaries_archived == 10
    assert season.summaries_normalized == 1
    assert season.issues == {"load": 9}
    assert len(season.unloaded_summaries) == 9
    assert not season.complete
    assert report.canary_findings == 0


def test_coverage_lists_final_games_with_no_archived_box_score(settings, sessions):
    feed = FakeFeed()
    feed.fail[("gameSummary", "")] = 404
    Backfill(client_for(settings, feed)).run([73])
    archive = RawArchive(settings.resolved_cache_dir)
    Normalizer(sessions, archive).run()

    with sessions() as session:
        report = build_report(session, archive)
    season = next(s for s in report.seasons if s.season_id == 73)

    assert season.summaries_archived == 0
    assert 33169 in season.missing_summaries
    assert report.to_dict()["complete"] is False
