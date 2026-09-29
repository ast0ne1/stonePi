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
    # Where visitors open published pages. Blank = worked out: behind the StonePi portal it's
    # PUBLIC_ORIGIN/files, standalone dev it's FILESERVE_URL.
    fileserve_public_url: str = Field(default="", validation_alias="FILESERVE_PUBLIC_URL")
    llm_model: str = Field(default="claude-sonnet-5", validation_alias="STUDIO_LLM_MODEL")
    # A full game (HTML + CSS + JS) routinely exceeds 8k tokens; truncated replies were
    # the main reason builds silently wrote nothing.
    llm_max_tokens: int = Field(default=64000, validation_alias="STUDIO_LLM_MAX_TOKENS")
    # gpt-4o-mini caps replies at ~16k tokens, which cuts most full game builds off.
    openai_model: str = Field(default="gpt-4.1", validation_alias="STUDIO_OPENAI_MODEL")


env = EnvSettings()
DATA_DIR = env.data_dir
WORKSPACE_DIR = DATA_DIR / "workspace"
PROMPT_PATH = ROOT_DIR / "app" / "prompts" / "fileserve_hosting.md"
PROMPTS_DIR = ROOT_DIR / "app" / "prompts"

BUILD_KINDS = (
    {
        "id": "game",
        "label": "Game",
        "tagline": "You make the rules.",
        "blurb": "Invent a game nobody has played before: a snake that eats stars, a dragon dodging storm clouds, a robot racing the clock. Describe it, and the AI builds it for you to play and improve.",
        "example": "e.g. Dragon Dash",
        "icon": "game",
        "intro": "Hi{name}! Let's invent a game together. 🎮\n\nTell me your idea: who's the hero, what's in their way, and how do you win? It can be as wild as you like. I'll ask a couple of questions. When you've finished describing, press **Build my game** and play it in the preview.",
        "ideas": [
            "A cat on a skateboard collecting fish while dodging puddles",
            "A snake that eats stars and glows brighter as it grows",
            "A dragon flying through storm clouds, catching raindrops",
            "A robot racing to fix broken lights before the timer runs out",
            "Breakout where the bricks are made of candy",
            "A penguin sliding down an endless icy hill",
            "A bee collecting pollen while avoiding spiders' webs",
            "A space rocket dodging asteroids to reach the moon",
            "A memory game with dinosaur cards",
        ],
    },
    {
        "id": "guide",
        "label": "Story or Guide",
        "tagline": "One page at a time.",
        "blurb": "Write a choose-your-own-adventure where the reader decides what happens next, or teach someone something step by step. Great for stories, quizzes and how-tos.",
        "example": "e.g. The Secret Forest",
        "icon": "guide",
        "intro": "Hi{name}! Let's make a story or guide. 📖\n\nIs it an adventure where the reader picks what happens next, or a step-by-step guide to teach something? Tell me who it's for and what it's about. When you've finished describing, press **Build my story**.",
        "ideas": [
            "A choose-your-path adventure in a secret forest",
            "A mystery where you help a detective dog find a lost key",
            "How to look after a hamster, step by step",
            "A space mission story where every choice changes the ending",
            "A quiz that teaches the planets of the solar system",
            "How to draw a cartoon cat in 6 steps",
            "A spooky (but not too scary) haunted house adventure",
            "How to reset the home Wi-Fi, step by step",
        ],
    },
    {
        "id": "spa",
        "label": "Handy App",
        "tagline": "Something genuinely useful.",
        "blurb": "Build a little tool you'll actually use: a pet-feeding chart, a spelling practice quiz, a countdown to your birthday, a family chore board. It remembers what you type in.",
        "example": "e.g. Birthday Countdown",
        "icon": "spa",
        "intro": "Hi{name}! Let's build a handy app. 🛠️\n\nWhat should it help with, and who will use it? Tell me what it needs to remember. When you've finished describing, press **Build my app** and try it in the preview.",
        "ideas": [
            "A countdown to my birthday with confetti on the day",
            "A spelling practice quiz with my weekly words",
            "A pet-feeding chart the whole family can tick off",
            "A chore board with stars for every job done",
            "A times-tables trainer that gets harder as I improve",
            "A packing checklist for holidays",
            "A family quiz night scoreboard",
            "A reading log that tracks the books I finish",
        ],
    },
)
BUILD_KIND_IDS = {item["id"] for item in BUILD_KINDS}
