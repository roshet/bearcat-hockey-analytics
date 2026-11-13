"""Run Alembic migrations from code (``bca db upgrade`` and the test suite)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from bca.config import PROJECT_ROOT


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Bring the database at ``url`` (default: ``Settings.database_url``) up to date."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    if url is not None:
        config.set_main_option("sqlalchemy.url", url)
    # Leave the caller's logging configuration alone.
    config.attributes["configure_logger"] = False
    command.upgrade(config, revision)
