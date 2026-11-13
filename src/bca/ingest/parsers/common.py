"""Coercion primitives shared by every feed parser.

The feed types the same kind of value differently from one view to the next --
team ``shots`` is an int, period ``homeShots`` is a string, booleans arrive as
``"0"``/``"1"`` or as JSON booleans, and "unknown" is spelled ``""``, ``None``
or ``0`` depending on the field. These validators are the one place that
knowledge lives (convention 3: coerce, never trust).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict


class FeedModel(BaseModel):
    """Base for every parsed feed object: lenient about extras, immutable."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    raise ValueError(f"expected an integer, got {value!r}")


def _optional_int(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    raise ValueError(f"expected a 0/1 flag, got {value!r}")


def _optional_date(value: Any) -> date | None:
    if value in (None, "", "0000-00-00"):
        return None
    return date.fromisoformat(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


FeedInt = Annotated[int, BeforeValidator(_int)]
"""An integer that may arrive as ``7`` or ``"7"``. Blank is an error."""

OptFeedInt = Annotated[int | None, BeforeValidator(_optional_int)]
"""An integer where ``""`` / ``None`` mean "not entered"."""

FeedBool = Annotated[bool, BeforeValidator(_bool)]
"""A flag that may arrive as ``"0"``/``"1"``, ``0``/``1`` or a JSON boolean."""

OptDate = Annotated[date | None, BeforeValidator(_optional_date)]
OptDateTime = Annotated[datetime | None, BeforeValidator(_optional_datetime)]

FeedText = Annotated[str, BeforeValidator(_text)]
"""A string where ``None`` means empty. Always stripped."""


_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def clock_to_seconds(clock: str) -> int:
    """``"4:39"`` / ``"00:23"`` -> seconds **elapsed** in the period.

    Elapsed, not remaining: play-by-play reports ``time "0:23"`` alongside
    ``s: 23`` for the same penalty, verified on game 33169.
    """
    match = _CLOCK.match(clock.strip())
    if not match:
        raise ValueError(f"unrecognised game clock {clock!r}")
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds >= 60:
        raise ValueError(f"unrecognised game clock {clock!r}")
    return minutes * 60 + seconds


def parse_period(period_id: Any) -> int:
    """Period ids as integers: 1-3 regulation, 4+ overtime.

    Anything else -- a shootout marker, say -- raises rather than being
    guessed at, so an unfamiliar shape surfaces as an ingest issue.
    """
    number = _int(period_id)
    if not 1 <= number <= 9:
        raise ValueError(f"unrecognised period id {period_id!r}")
    return number
