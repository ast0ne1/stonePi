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
    port: int = 8005
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    fileserve_url: str = Field(default="http://127.0.0.1:8002", validation_alias="FILESERVE_URL")
    llm_model: str = Field(default="claude-sonnet-4-20250514", validation_alias="STUDIO_LLM_MODEL")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="STUDIO_OPENAI_MODEL")


env = EnvSettings()
DATA_DIR = env.data_dir
WORKSPACE_DIR = DATA_DIR / "workspace"
PROMPT_PATH = ROOT_DIR / "app" / "prompts" / "fileserve_hosting.md"
PROMPTS_DIR = ROOT_DIR / "app" / "prompts"

BUILD_KINDS = (
    {
        "id": "spa",
        "label": "Single Page App",
        "blurb": "One phone-friendly screen that does a job — checklist, quiz, or little tool.",
        "icon": "spa",
    },
    {
        "id": "guide",
        "label": "Interactive Guide",
        "blurb": "Step-by-step how-to or choose-your-path story, sized for small phones.",
        "icon": "guide",
    },
    {
        "id": "game",
        "label": "Game",
        "blurb": "A real playable game for iPhone 13+ and laptops — canvas, score, keyboard + touch controls.",
        "icon": "game",
    },
)
BUILD_KIND_IDS = {item["id"] for item in BUILD_KINDS}
