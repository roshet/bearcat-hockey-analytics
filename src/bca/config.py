"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration.

    Every field is overridable by a ``BCA_``-prefixed environment variable so that
    local development, CI and the deployed backend share one code path.
    """

    model_config = SettingsConfigDict(
        env_prefix="BCA_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://bca:bca@localhost:5433/bca"

    # --- ACHA / HockeyTech LeagueStat feed ---
    acha_client_code: str = "acha"
    #: The key achahockey.org's own pages use. Set in .env; never committed.
    acha_api_key: str = ""
    acha_feed_base_url: str = "https://lscluster.hockeytech.com/feed/index.php"

    # --- Politeness / robustness ---
    # We are a guest on someone else's infrastructure. These defaults are
    # deliberately conservative; see docs/data-sources.md.
    request_delay_seconds: float = Field(default=1.0, ge=0.0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=4, ge=0)
    contact_email: str = ""

    # --- Local HTTP cache ---
    cache_dir: Path = Path("data/cache")
    cache_enabled: bool = True

    # --- Official ranking PDFs the user downloads (the ACHA site refuses our downloader) ---
    rankings_inbox: Path = Path.home() / "Downloads"

    @property
    def user_agent(self) -> str:
        """Identify ourselves honestly and give the operator a way to reach us."""
        return (
            f"BearcatHockeyAnalytics/{_version()} "
            f"(student research project; contact: {self.contact_email})"
        )

    @property
    def has_usable_contact_email(self) -> bool:
        """Whether ``contact_email`` is a real address rather than a stand-in.

        ``.env.example`` ships a placeholder, so a fresh clone would otherwise
        send thousands of effectively anonymous requests to the ACHA's host.
        """
        email = self.contact_email.strip().lower()
        if not email or "@" not in email:
            return False
        if email.endswith(("@example.com", "@example.org", ".invalid")):
            return False
        return "changeme" not in email

    @property
    def resolved_cache_dir(self) -> Path:
        if self.cache_dir.is_absolute():
            return self.cache_dir
        return PROJECT_ROOT / self.cache_dir


class ConfigurationError(Exception):
    """The project is configured in a way that must not be allowed to run."""


def require_contact_email(settings: Settings) -> None:
    """Refuse to touch the network without a real contact address.

    We read an undocumented API on a volunteer league's server using a key
    lifted from their own pages. The one courtesy that makes that defensible is
    telling the operator who we are and how to make us stop -- otherwise their
    only remedy is a silent block, and we never learn why the data dried up.

    This is enforced in code rather than documented as a convention because a
    convention is exactly what gets skipped at 2am during a backfill.
    """
    if settings.has_usable_contact_email:
        return

    shown = settings.contact_email.strip() or "(unset)"
    raise ConfigurationError(
        f"BCA_CONTACT_EMAIL is not a usable address: {shown}\n"
        "\n"
        "Set a real address in .env before any request is sent. It goes into the\n"
        "User-Agent on every call to the ACHA's server and is how they reach you\n"
        "instead of silently blocking the API key.\n"
        "\n"
        "    BCA_CONTACT_EMAIL=you@mail.uc.edu\n"
        "\n"
        "Refusing to send anonymous requests."
    )


def require_feed_key(settings: Settings) -> None:
    """Refuse to touch the network without the feed key (it is never committed)."""
    if not settings.acha_api_key.strip():
        raise ConfigurationError(
            "BCA_ACHA_API_KEY is not set. Set it in .env before any request is sent."
        )


def _version() -> str:
    from bca import __version__

    return __version__


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
