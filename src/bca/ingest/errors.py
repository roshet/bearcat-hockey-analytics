"""Exceptions raised by the ingestion layer."""

from __future__ import annotations


class IngestError(Exception):
    """Base class for all ingestion failures."""


class FeedTransportError(IngestError):
    """The request could not be completed (network, timeout, 5xx after retries)."""


class FeedAuthError(IngestError):
    """The feed rejected our API key.

    The ACHA key is publicly embedded in achahockey.org's pages and is not
    guaranteed to be stable. If this is raised, the key has very likely rotated
    and must be re-read from the league site.
    """


class FeedResponseError(IngestError):
    """The feed returned a structured error, e.g. an unknown ``view``."""


class FeedParseError(IngestError):
    """The response body could not be decoded into JSON."""
