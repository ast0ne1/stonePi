from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"

SPORTS: tuple[dict[str, str], ...] = (
    {"id": "all", "label": "All", "image": "all.svg"},
    {"id": "afl", "label": "AFL", "image": "afl.svg"},
    {"id": "cricket", "label": "Cricket", "image": "cricket.svg"},
    {"id": "rugby", "label": "Rugby", "image": "rugby.svg"},
    {"id": "football", "label": "Football", "image": "football.svg"},
)

FOOTBALL_LEAGUES: tuple[str, ...] = (
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "Champions League",
    "Europa League",
    "Conference League",
    "Other",
)

COMMON_TIMEZONES: tuple[str, ...] = (
    "Australia/Melbourne",
    "Australia/Sydney",
    "Australia/Brisbane",
    "Australia/Perth",
    "Europe/London",
    "Europe/Copenhagen",
    "Europe/Berlin",
    "Europe/Amsterdam",
    "Europe/Paris",
    "Europe/Madrid",
    "Europe/Rome",
    "UTC",
)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8007
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    # Daily auto-refresh hour in the household timezone (0–23). Manual refresh always allowed.
    daily_refresh_hour: int = Field(default=6, validation_alias="SPORTGUIDE_DAILY_REFRESH_HOUR")
    look_ahead_hours: int = Field(default=24, validation_alias="SPORTGUIDE_LOOK_AHEAD_HOURS")


env = EnvSettings()
DATA_DIR = env.data_dir
DB_PATH = DATA_DIR / "sportguide.sqlite"
