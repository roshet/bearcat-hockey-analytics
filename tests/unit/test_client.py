"""Tests for the feed client.

The decoding tests matter more than they look. Each one pins a real behaviour of
the ACHA feed that was discovered by probing it, and that would otherwise be
rediscovered painfully halfway through a backfill.
"""

from __future__ import annotations

import json

import httpx
import pytest

from bca.config import ConfigurationError, Settings, require_contact_email
from bca.ingest.client import FeedRequest, HockeyTechClient
from bca.ingest.errors import (
    FeedAuthError,
    FeedParseError,
    FeedResponseError,
    FeedTransportError,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url="postgresql+psycopg://unused@localhost/unused",
        cache_dir=tmp_path / "cache",
        cache_enabled=True,
        request_delay_seconds=0.0,
        max_retries=2,
        contact_email="maintainer@bca.test",
        acha_api_key="test-key",
    )


def make_client(settings, handler):
    return HockeyTechClient(settings, transport=httpx.MockTransport(handler))


def respond(body: str, status: int = 200):
    def handler(_request):
        return httpx.Response(status, text=body)

    return handler


# --- request identity ------------------------------------------------------


def test_cache_key_ignores_credentials_but_tracks_params():
    a = FeedRequest("modulekit", {"view": "roster", "team_id": "265"})
    b = FeedRequest("modulekit", {"team_id": "265", "view": "roster"})
    c = FeedRequest("modulekit", {"view": "roster", "team_id": "346"})

    assert a.cache_key == b.cache_key, "parameter order must not change identity"
    assert a.cache_key != c.cache_key, "a different team is a different resource"


def test_identity_is_readable():
    request = FeedRequest("modulekit", {"view": "schedule", "team_id": "265", "season_id": "73"})
    assert request.identity == "modulekit:schedule:season_id=73:team_id=265"


def test_query_always_requests_json():
    assert FeedRequest("gc", {"tab": "pxpverbose"}).query()["fmt"] == "json"


# --- decoding the feed's real response shapes ------------------------------


def test_modulekit_payload_is_plain_json(settings):
    body = json.dumps({"SiteKit": {"Seasons": [{"season_id": "73"}]}})
    client = make_client(settings, respond(body))
    response = client.fetch(FeedRequest("modulekit", {"view": "seasons"}))

    assert response.payload["SiteKit"]["Seasons"][0]["season_id"] == "73"
    assert response.from_cache is False


def test_statviewfeed_parenthesis_wrapper_is_stripped(settings):
    # The real feed returns ([{...}]) for statviewfeed views, which is not JSON.
    body = '([{"sections":[{"data":[{"row":{"goals":"7"}}]}]}])'
    client = make_client(settings, respond(body))
    response = client.fetch(FeedRequest("statviewfeed", {"view": "players"}))

    assert response.payload[0]["sections"][0]["data"][0]["row"]["goals"] == "7"
    assert response.is_empty is False


def test_empty_statviewfeed_payload_is_detected_not_raised(settings):
    # A view that exists but has no rows returns HTTP 200 with empty sections.
    client = make_client(settings, respond('([{"sections":[]}])'))
    response = client.fetch(FeedRequest("statviewfeed", {"view": "teams"}))

    assert response.is_empty is True


def test_invalid_key_text_body_raises_auth_error(settings):
    client = make_client(settings, respond("Invalid key."))
    with pytest.raises(FeedAuthError, match="rotated"):
        client.fetch(FeedRequest("modulekit", {"view": "seasons"}))


def test_structured_error_object_raises_response_error(settings):
    client = make_client(settings, respond('{"error":"InvalidView error: standings"}'))
    with pytest.raises(FeedResponseError, match="InvalidView"):
        client.fetch(FeedRequest("statviewfeed", {"view": "standings"}))


def test_undecodable_body_raises_parse_error_with_preview(settings):
    client = make_client(settings, respond("<html>503 from a proxy</html>"))
    with pytest.raises(FeedParseError, match="503 from a proxy"):
        client.fetch(FeedRequest("modulekit", {"view": "seasons"}))


def test_empty_body_raises_parse_error(settings):
    client = make_client(settings, respond("   "))
    with pytest.raises(FeedParseError, match="empty body"):
        client.fetch(FeedRequest("modulekit", {"view": "seasons"}))


# --- retries ---------------------------------------------------------------


def test_server_errors_are_retried_then_succeed(settings):
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, text='{"SiteKit":{"ok":1}}')

    client = make_client(settings, handler)
    response = client.fetch(FeedRequest("modulekit", {"view": "seasons"}))

    assert calls["n"] == 3
    assert response.payload["SiteKit"]["ok"] == 1


def test_persistent_server_error_gives_up(settings):
    client = make_client(settings, respond("down", status=500))
    with pytest.raises(FeedTransportError, match="giving up"):
        client.fetch(FeedRequest("modulekit", {"view": "seasons"}))


def test_client_errors_are_not_retried(settings):
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, text="nope")

    client = make_client(settings, handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch(FeedRequest("modulekit", {"view": "seasons"}))

    assert calls["n"] == 1, "a 404 will not improve by asking again"


# --- caching ---------------------------------------------------------------


def test_second_fetch_is_served_from_cache(settings):
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, text='{"SiteKit":{"ok":1}}')

    client = make_client(settings, handler)
    request = FeedRequest("modulekit", {"view": "seasons"})

    first = client.fetch(request)
    second = client.fetch(request)

    assert calls["n"] == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.payload == first.payload


def test_no_cache_bypasses_the_cache(settings):
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, text='{"SiteKit":{"ok":1}}')

    client = make_client(settings, handler)
    request = FeedRequest("modulekit", {"view": "seasons"})

    client.fetch(request, use_cache=False)
    client.fetch(request, use_cache=False)

    assert calls["n"] == 2


def test_corrupt_cache_entry_is_discarded_not_fatal(settings):
    client = make_client(settings, respond('{"SiteKit":{"ok":1}}'))
    request = FeedRequest("modulekit", {"view": "seasons"})

    client.fetch(request)
    cache_file = next(settings.resolved_cache_dir.glob("*.json"))
    cache_file.write_text("{ truncated", encoding="utf-8")

    response = client.fetch(request)

    assert response.payload["SiteKit"]["ok"] == 1
    assert response.from_cache is False


# --- politeness ------------------------------------------------------------


def test_user_agent_identifies_us_and_how_to_make_us_stop(settings):
    client = make_client(settings, respond('{"SiteKit":{}}'))
    agent = client.settings.user_agent

    assert "BearcatHockeyAnalytics" in agent
    assert "maintainer@bca.test" in agent


@pytest.mark.parametrize(
    "email",
    [
        "",
        "   ",
        "not-an-email",
        "you@example.com",  # the placeholder shipped in .env.example
        "YOU@EXAMPLE.COM",
        "someone@example.org",
        "changeme@mail.uc.edu",
        "test@foo.invalid",
    ],
)
def test_client_refuses_to_exist_without_a_real_contact_address(settings, email):
    """No code path may reach the network anonymously.

    The guard lives in the client constructor rather than in a CLI command so
    that future ingestion code cannot bypass it by forgetting to call a check.
    """
    anonymous = settings.model_copy(update={"contact_email": email})

    with pytest.raises(ConfigurationError, match="BCA_CONTACT_EMAIL"):
        HockeyTechClient(anonymous, transport=httpx.MockTransport(respond("{}")))


def test_client_refuses_without_the_feed_key(settings):
    keyless = settings.model_copy(update={"acha_api_key": " "})
    with pytest.raises(ConfigurationError, match="BCA_ACHA_API_KEY"):
        HockeyTechClient(keyless, transport=httpx.MockTransport(respond("{}")))


@pytest.mark.parametrize(
    "email",
    ["student@mail.uc.edu", "a.b+tag@uc.edu", "maintainer@bca.test"],
)
def test_real_addresses_are_accepted(settings, email):
    usable = settings.model_copy(update={"contact_email": email})
    assert usable.has_usable_contact_email is True
    require_contact_email(usable)  # must not raise


def test_guard_error_message_tells_you_how_to_fix_it(settings):
    anonymous = settings.model_copy(update={"contact_email": ""})

    with pytest.raises(ConfigurationError) as caught:
        require_contact_email(anonymous)

    message = str(caught.value)
    assert "BCA_CONTACT_EMAIL=" in message, "must show the variable to set"
    assert ".env" in message, "must say where to set it"
    assert "(unset)" in message, "must show what it currently is"
