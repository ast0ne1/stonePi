from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8013
    # ADB connector (pything's method). Off in run-dev unless CARTHING_ADB=1 and adb is on PATH.
    adb_enabled: bool = Field(default=True, validation_alias=AliasChoices("CARTHING_ADB", "ADB_ENABLED"))
    adb_path: str = Field(default="adb", validation_alias=AliasChoices("CARTHING_ADB_PATH", "ADB_PATH"))
    notify_url: str = Field(default="http://127.0.0.1:8012", validation_alias=AliasChoices("STONEPI_NOTIFY_URL", "NOTIFY_URL"))
    dashboard_url: str = Field(
        default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_DASHBOARD_URL", "DASHBOARD_URL")
    )
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    # Outbound weather (Open-Meteo, no key). Empty disables weather.
    weather_url: str = "https://api.open-meteo.com/v1/forecast"


env = EnvSettings()
DATA_DIR = env.data_dir
