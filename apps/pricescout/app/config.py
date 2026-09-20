from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"

# eTilbudsavis / Tjek dealer ids (DK groceries)
STORES: tuple[dict[str, str], ...] = (
    {"id": "netto", "name": "Netto", "dealer_id": "9ba51"},
    {"id": "lidl", "name": "Lidl", "dealer_id": "71c90"},
    {"id": "discount365", "name": "365discount", "dealer_id": "DWZE1w"},
    {"id": "foetex", "name": "føtex", "dealer_id": "bdf5A"},
    {"id": "kvickly", "name": "Kvickly", "dealer_id": "c1edq"},
)

CATEGORIES: tuple[str, ...] = (
    "Dairy",
    "Meat & fish",
    "Fruit & veg",
    "Bread & bakery",
    "Drinks",
    "Pantry",
    "Frozen",
    "Household",
    "Other",
)

# Display currencies (leaflet prices are DKK; others convert for display only)
CURRENCIES: tuple[dict[str, str], ...] = (
    {"id": "DKK", "label": "Danish krone (kr)", "symbol": "kr"},
    {"id": "EUR", "label": "Euro (€)", "symbol": "€"},
    {"id": "SEK", "label": "Swedish krona (kr)", "symbol": "kr"},
    {"id": "NOK", "label": "Norwegian krone (kr)", "symbol": "kr"},
    {"id": "GBP", "label": "Pound sterling (£)", "symbol": "£"},
    {"id": "USD", "label": "US dollar ($)", "symbol": "$"},
)
# Approximate rates from DKK for display (not live FX)
DKK_TO: dict[str, float] = {
    "DKK": 1.0,
    "EUR": 0.134,
    "SEK": 1.52,
    "NOK": 1.55,
    "GBP": 0.115,
    "USD": 0.145,
}


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8006
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    mock: bool = Field(default=False, validation_alias=AliasChoices("PRICESCOUT_MOCK", "MOCK"))
    tjek_api_key: str = Field(default="", validation_alias=AliasChoices("TJEK_API_KEY", "ETILBUDSAVIS_API_KEY"))
    salling_api_token: str = Field(default="", validation_alias=AliasChoices("SALLING_API_TOKEN", "SALLING_TOKEN"))
    refresh_hours: float = Field(default=6.0, validation_alias="PRICESCOUT_REFRESH_HOURS")
    max_offers_per_source: int = Field(default=400, validation_alias="PRICESCOUT_MAX_OFFERS")
    pinboard_url: str = Field(default="http://127.0.0.1:8004", validation_alias="PINBOARD_URL")


env = EnvSettings()
DATA_DIR = env.data_dir
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "pricescout.sqlite"
