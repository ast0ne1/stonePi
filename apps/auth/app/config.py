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
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))


env = EnvSettings()
DATA_DIR = env.data_dir
