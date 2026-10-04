from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"

SCHEDULE_OPTIONS: tuple[dict[str, int | str], ...] = (
    {"id": "60", "label": "Every hour", "minutes": 60},
    {"id": "180", "label": "Every 3 hours", "minutes": 180},
    {"id": "360", "label": "Every 6 hours", "minutes": 360},
    {"id": "720", "label": "Every 12 hours", "minutes": 720},
    {"id": "1440", "label": "Daily", "minutes": 1440},
)

CONDITION_OPTIONS: tuple[dict[str, str], ...] = (
    {"id": "new", "label": "New only"},
    {"id": "used", "label": "Used only"},
    {"id": "either", "label": "New or used"},
)

WATCH_STATUSES = ("watching", "strike_found", "paused", "error", "no_results")

DEFAULT_SCHEDULE_MINUTES = 360
DEFAULT_CONDITION = "new"
DEFAULT_RETENTION_DAYS = 90
SCAN_CACHE_TTL_SECONDS = 15 * 60

# Trust scores (Trustpilot via Bright Data, PriceRunner shop rating as fallback).
DEFAULT_TRUST_MIN_REVIEWS = 50
DEFAULT_TRUST_REFRESH_DAYS = 7
MIN_SCORE_OPTIONS: tuple[dict[str, str | float | None], ...] = (
    {"id": "", "label": "Any", "value": None},
    {"id": "3.5", "label": "3.5+", "value": 3.5},
    {"id": "4.0", "label": "4.0+", "value": 4.0},
    {"id": "4.5", "label": "4.5+", "value": 4.5},
)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8008
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    mock: bool = Field(default=False, validation_alias=AliasChoices("PRICEWATCH_MOCK", "MOCK"))
    request_timeout: float = Field(default=20.0, validation_alias="PRICEWATCH_TIMEOUT")
    min_request_interval: float = Field(default=1.0, validation_alias="PRICEWATCH_MIN_INTERVAL")


env = EnvSettings()
DATA_DIR = env.data_dir
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "pricewatch.sqlite"
