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
    port: int = 8016
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    mock: bool = Field(default=False, validation_alias=AliasChoices("RADIO_MOCK", "MOCK"))
    odio_api_url: str = Field(
        default="http://127.0.0.1:8018",
        validation_alias=AliasChoices("ODIO_API_URL", "RADIO_ODIO_URL"),
    )
    # myMPD (Odio webradio browse + WebradioDB artwork). Same host as Odio; not nginx-proxied.
    mympd_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("MYMPD_URL", "RADIO_MYMPD_URL"),
    )
    default_volume: float = Field(default=0.6, validation_alias="RADIO_DEFAULT_VOLUME")


env = EnvSettings()
DATA_DIR = env.data_dir
DB_PATH = DATA_DIR / "radio.sqlite"
