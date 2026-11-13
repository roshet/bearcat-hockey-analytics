"""Engine and session construction."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from bca.config import Settings, get_settings


def make_engine(url: str | None = None, settings: Settings | None = None) -> Engine:
    """Build an engine. SQLite (used in tests) gets foreign keys switched on."""
    url = url or (settings or get_settings()).database_url
    engine = create_engine(url, future=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_fks(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
