"""Fixtures shared by the database-backed unit tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from bca.db.migrate import upgrade
from bca.db.session import make_engine, make_session_factory


@pytest.fixture
def sessions(tmp_path) -> sessionmaker[Session]:
    """A SQLite database built through the Alembic migrations, not ``create_all``,
    so every test that uses it also proves the migrations match the models."""
    url = f"sqlite:///{(tmp_path / 'bca.db').as_posix()}"
    upgrade(url)
    return make_session_factory(make_engine(url))
