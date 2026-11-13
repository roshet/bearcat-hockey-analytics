"""Client for the ACHA's HockeyTech / LeagueStat JSON feed.

Why this module exists in this shape
------------------------------------
The feed is undocumented and quietly inconsistent. Three behaviours discovered
while investigating it drive the design here, and each is handled in exactly one
place so the rest of the codebase never has to think about them:

1. ``feed=statviewfeed`` responses are wrapped in parentheses -- ``([{...}])`` --
   which is not valid JSON. ``feed=modulekit`` responses are not wrapped.
2. Errors arrive in at least three shapes: a bare text body (``Invalid key.``),
   a JSON object (``{"error": "InvalidView error: standings"}``), and an
   HTTP-200 empty payload (``([{"sections":[]}])``) for a view that exists but
   has no data.
3. The API key is publicly embedded in the league's own pages and may rotate at
   any time, so key rejection is surfaced as its own error type.

We are an uninvited guest on someone else's infrastructure, so every request is
rate limited, retried with backoff, cached on disk, and sent with a User-Agent
that says who we are and how to reach us.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from bca.config import Settings, get_settings, require_contact_email, require_feed_key
from bca.ingest.archive import RawArchive
from bca.ingest.errors import (
    FeedAuthError,
    FeedParseError,
    FeedResponseError,
    FeedTransportError,
)

logger = logging.getLogger(__name__)

#: Error bodies the feed returns as plain text rather than JSON.
_AUTH_ERROR_BODIES = frozenset({"invalid key", "invalid key."})

#: Parameters excluded from the cache key: they identify us, not the resource.
_NON_IDENTIFYING_PARAMS = frozenset({"key", "client_code"})


@dataclass(frozen=True, slots=True)
class FeedRequest:
    """A single addressable feed resource.

    ``params`` holds only the query parameters that identify *what* is being
    requested. Credentials are added at send time so they never leak into cache
    keys or the archive's natural identity.
    """

    feed: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def view(self) -> str:
        """The ``view`` or ``tab`` discriminator, for logging and archiving."""
        return self.params.get("view") or self.params.get("tab") or ""

    def query(self) -> dict[str, str]:
        return {"feed": self.feed, **self.params, "fmt": "json"}

    @property
    def identity(self) -> str:
        """Stable, human-readable identity. Used for logs and the archive."""
        bits = [self.feed, self.view] if self.view else [self.feed]
        extras = sorted(
            f"{k}={v}"
            for k, v in self.params.items()
            if k not in {"view", "tab"} and k not in _NON_IDENTIFYING_PARAMS
        )
        return ":".join([*bits, *extras])

    @property
    def cache_key(self) -> str:
        payload = json.dumps(
            {"feed": self.feed, "params": dict(sorted(self.params.items()))},
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
        safe_view = self.view or "feed"
        return f"{self.feed}.{safe_view}.{digest}"


@dataclass(frozen=True, slots=True)
class FeedResponse:
    """A decoded feed response plus the provenance needed to archive it."""

    request: FeedRequest
    payload: Any
    fetched_at: datetime
    from_cache: bool
    raw_text: str

    @property
    def is_empty(self) -> bool:
        """True when the view exists but returned no rows.

        The feed signals this with an HTTP 200 and an empty ``sections`` list
        rather than a 404, so callers must check it explicitly.
        """
        payload = self.payload
        if payload is None or payload == [] or payload == {}:
            return True
        if isinstance(payload, list) and len(payload) == 1:
            only = payload[0]
            if isinstance(only, dict) and only.get("sections") == []:
                return True
        return False


class HockeyTechClient:
    """Rate-limited, retrying, caching reader for the LeagueStat feed.

    Usable as a context manager. Not thread-safe -- the rate limiter holds a
    single timestamp -- which is deliberate: concurrent requests to a volunteer
    league's stats host are not something we want to make easy.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # Enforced at construction so no code path -- present or future -- can
        # reach the network without identifying us. Raises ConfigurationError.
        require_contact_email(self.settings)
        require_feed_key(self.settings)
        self._client = httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )
        self._last_request_at: float | None = None
        self.archive = RawArchive(self.settings.resolved_cache_dir)

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> HockeyTechClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- public API --------------------------------------------------------

    def fetch(
        self,
        request: FeedRequest,
        *,
        use_cache: bool | None = None,
        refresh: bool = False,
    ) -> FeedResponse:
        """Fetch one feed resource, preferring the on-disk cache.

        ``use_cache=False`` neither reads nor writes the archive (exploration).
        ``refresh=True`` skips the read but still archives the new response, so a
        changed resource keeps its previous version in history.
        """
        cache_enabled = self.settings.cache_enabled if use_cache is None else use_cache

        if cache_enabled and not refresh:
            cached = self._read_cache(request)
            if cached is not None:
                logger.debug("cache hit %s", request.identity)
                return cached

        raw_text = self._send_with_retries(request)
        payload = _decode(raw_text, request)
        response = FeedResponse(
            request=request,
            payload=payload,
            fetched_at=datetime.now(UTC),
            from_cache=False,
            raw_text=raw_text,
        )
        if cache_enabled:
            self._write_cache(response)
        return response

    def is_cached(self, request: FeedRequest) -> bool:
        """Whether a fetch would be served from the archive (costs no request)."""
        return self.archive.path_for(request.cache_key).exists()

    # -- transport ---------------------------------------------------------

    def _send_with_retries(self, request: FeedRequest) -> str:
        query = {
            **request.query(),
            "key": self.settings.acha_api_key,
            "client_code": self.settings.acha_client_code,
        }
        attempts = self.settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._respect_rate_limit()
            try:
                http_response = self._client.get(self.settings.acha_feed_base_url, params=query)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "transport error on %s (attempt %d/%d): %s",
                    request.identity,
                    attempt,
                    attempts,
                    exc,
                )
            else:
                # 4xx is our fault and will not improve by retrying.
                if http_response.status_code < 500:
                    http_response.raise_for_status()
                    return http_response.text
                last_error = FeedTransportError(
                    f"HTTP {http_response.status_code} from {request.identity}"
                )
                logger.warning(
                    "server error %d on %s (attempt %d/%d)",
                    http_response.status_code,
                    request.identity,
                    attempt,
                    attempts,
                )

            if attempt < attempts:
                time.sleep(min(2.0 ** (attempt - 1), 30.0))

        raise FeedTransportError(
            f"giving up on {request.identity} after {attempts} attempts"
        ) from last_error

    def _respect_rate_limit(self) -> None:
        delay = self.settings.request_delay_seconds
        if delay <= 0 or self._last_request_at is None:
            self._last_request_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_at = time.monotonic()

    # -- cache ---------------------------------------------------------------

    def _read_cache(self, request: FeedRequest) -> FeedResponse | None:
        archived = self.archive.read(request.cache_key)
        if archived is None:
            return None
        return FeedResponse(
            request=request,
            payload=archived.payload,
            fetched_at=archived.fetched_at,
            from_cache=True,
            raw_text=archived.raw_text,
        )

    def _write_cache(self, response: FeedResponse) -> None:
        self.archive.write(
            response.request.cache_key,
            identity=response.request.identity,
            feed=response.request.feed,
            params=response.request.params,
            fetched_at=response.fetched_at,
            payload=response.payload,
            raw_text=response.raw_text,
        )


# -- response decoding -----------------------------------------------------


def _decode(raw_text: str, request: FeedRequest) -> Any:
    """Turn a feed body into Python, normalising the feed's error shapes."""
    body = raw_text.strip()

    if not body:
        raise FeedParseError(f"empty body for {request.identity}")

    if body.lower() in _AUTH_ERROR_BODIES:
        raise FeedAuthError(
            "the ACHA feed rejected our API key; it has most likely rotated and "
            "must be re-read from achahockey.org"
        )

    # statviewfeed wraps its payload in parentheses, which is not valid JSON.
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body[:200]
        raise FeedParseError(
            f"could not decode response for {request.identity}: {exc}; "
            f"body starts: {preview!r}"
        ) from exc

    if isinstance(payload, dict) and payload.get("error"):
        raise FeedResponseError(f"{request.identity}: {payload['error']}")

    return payload
