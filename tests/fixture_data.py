"""Recorded ACHA data the tests read.

The feed responses and ranking PDFs under ``tests/fixtures/`` are the league's data, so they
are kept out of the public copy of this project. A test that needs one skips, with the
reason, when the file is absent, rather than failing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_file(rel: str) -> Path:
    """The path to a recorded file, or skip the calling test if it isn't here."""
    path = FIXTURES / rel
    if not path.exists():
        pytest.skip(f"needs recorded ACHA data ({rel}), which is not redistributed")
    return path


def fixture_json(rel: str) -> Any:
    return json.loads(fixture_file(rel).read_text(encoding="utf-8"))
