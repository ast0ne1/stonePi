from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"
DEFAULT_COCKPIT_URL = "https://stonepi.local:9090"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8011
    database_url: str = "sqlite:///./data/users.sqlite"
    public_origin: str = Field(
        default="http://127.0.0.1:8010",
        validation_alias=AliasChoices("PUBLIC_ORIGIN", "STONEPI_PUBLIC_ORIGIN"),
    )
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = ""
    admin_username: str = "admin"
    admin_password: str = "admin"
    admin_display_name: str = "Admin"
    stonepi_prefix: str = ""
    cockpit_url: str = DEFAULT_COCKPIT_URL
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))


env = EnvSettings()
DATA_DIR = env.data_dir


def _as_https_cockpit(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_COCKPIT_URL
    if raw.startswith("http://"):
        return "https://" + raw[len("http://") :]
    if raw.startswith("https://"):
        return raw
    return f"https://{raw.lstrip('/')}"


def resolve_cockpit_url(request_host: str | None = None) -> str:
    """Cockpit HTTPS link for the Auth hub (same rules as dashboard)."""
    configured = (env.cockpit_url or "").strip()
    parsed = urlparse(configured) if configured else None
    configured_host = (parsed.hostname or "").lower() if parsed else ""

    if configured and configured_host and configured_host not in ("127.0.0.1", "localhost"):
        return _as_https_cockpit(configured)

    hostname = ""
    if request_host:
        hostname = request_host.split(",")[0].strip().split(":")[0].strip()
    if hostname and hostname not in ("127.0.0.1", "localhost"):
        return f"https://{hostname}:9090"

    return _as_https_cockpit(configured or DEFAULT_COCKPIT_URL)
