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

    host: str = "0.0.0.0"
    port: int = 8085
    database_url: str = "sqlite:///./data/eventtrakr.db"
    public_base_url: str = "http://127.0.0.1:8085"
    device_hostname: str = ""

    admin_username: str = "admin"
    admin_password: str = "admin"
    session_secret: str = ""

    default_location: str = "Copenhagen, Denmark"
    default_sync_interval_minutes: int = 60
    max_lookahead_days: int = 7

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8085/calendar/google/callback"

    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    facebook_redirect_uri: str = "http://127.0.0.1:8085/calendar/facebook/callback"

    stonepi_session_secret: str = ""
    stonepi_prefix: str = ""
    stonepi_auth_url: str = ""
    stonepi_app_id: str = "eventtrakr"
    stonepi_public_origin: str = ""
    github_repo: str = ""
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))


env = EnvSettings()
DATA_DIR = env.data_dir
TLS_DIR = DATA_DIR / "tls"
CACHE_DIR = DATA_DIR / "cache"
