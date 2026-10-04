from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"

GIB = 1024**3

# Featured titles. `{lang}` is the two-letter language code from Settings → Content.
# Every other Kiwix title can still be installed by name or link.
FEATURED: tuple[dict[str, str], ...] = (
    {"key": "wikipedia", "name": "wikipedia_{lang}_all", "label": "Wikipedia", "blurb": "The free encyclopedia.", "icon": "encyclopedia"},
    {"key": "wikivoyage", "name": "wikivoyage_{lang}_all", "label": "Wikivoyage", "blurb": "Travel guides for places worldwide.", "icon": "travel"},
    {"key": "wiktionary", "name": "wiktionary_{lang}_all", "label": "Wiktionary", "blurb": "Dictionary and thesaurus.", "icon": "dictionary"},
    {"key": "gutenberg", "name": "gutenberg_{lang}_all", "label": "Project Gutenberg", "blurb": "Public-domain books.", "icon": "books"},
    # Tiny real ZIM (about 300 MB): a quick way to check setup end to end.
    {"key": "sample", "name": "wikipedia_{lang}_100", "label": "Wikipedia sample", "blurb": "Top 100 articles — try the Library quickly.", "icon": "sample"},
)

LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("da", "Dansk"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("nl", "Nederlands"),
    ("nb", "Norsk"),
    ("sv", "Svenska"),
)

# Kiwix flavours, most complete first.
FLAVOUR_LABELS = {
    "maxi": "Full, with images",
    "nopic": "Text only",
    "mini": "Top articles, intros only",
    "": "Standard",
}
FLAVOUR_ORDER = ("maxi", "", "nopic", "mini")

FILESYSTEMS_OK = ("ext4", "exfat")
BACKUP_LABEL = "STONEPI-BACKUP"
BACKUP_MOUNT = Path("/mnt/stonepi-backup")
LIBRARY_MOUNT = Path("/mnt/stonepi-library")
# Folder used on an external drive (and on the backup drive when shared).
DRIVE_SUBDIR = "StonePi-Library/zim"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8009
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    public_origin: str = Field(default="http://127.0.0.1:8010", validation_alias=AliasChoices("STONEPI_PUBLIC_ORIGIN", "PUBLIC_ORIGIN"))
    hostname: str = Field(default="stonepi", validation_alias=AliasChoices("STONEPI_HOSTNAME", "HOSTNAME"))
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    stonepi_prefix: str = Field(default="", validation_alias=AliasChoices("STONEPI_PREFIX", "PREFIX"))
    routing: str = "path"
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    catalog_url: str = Field(default="https://opds.library.kiwix.org", validation_alias="LIBRARY_CATALOG_URL")
    kiwix_url: str = Field(default="http://127.0.0.1:8014", validation_alias="LIBRARY_KIWIX_URL")
    # Public path Kiwix is served under (kiwix-serve --urlRootLocation).
    reader_path: str = Field(default="/library/read", validation_alias="LIBRARY_READER_PATH")
    helper: str = Field(default="/usr/local/sbin/stonepi-library-helper", validation_alias="LIBRARY_HELPER")
    backup_conf: Path = Field(default=Path("/etc/stonepi/backup.conf"), validation_alias="LIBRARY_BACKUP_CONF")
    backup_stamp: Path = Field(default=Path("/var/lib/stonepi/last-usb-backup.txt"), validation_alias="LIBRARY_BACKUP_STAMP")
    request_timeout: float = Field(default=30.0, validation_alias="LIBRARY_TIMEOUT")


env = EnvSettings()
DATA_DIR = env.data_dir
DB_PATH = DATA_DIR / "library.sqlite"
LIBRARY_XML = DATA_DIR / "library.xml"
CATALOG_CACHE = DATA_DIR / "catalog.json"
# Read by stonepi-backup (root) to copy content; written on every install/remove.
BACKUP_MANIFEST = DATA_DIR / "backup-manifest.json"
DEFAULT_CONTENT_DIR = DATA_DIR / "zim"
