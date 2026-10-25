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

    # Loopback only on the Pi: devices arrive through nginx (LAN) or Tailscale Funnel (away).
    host: str = "127.0.0.1"
    port: int = 8015
    session_secret: str = Field(default="", validation_alias=AliasChoices("STONEPI_SESSION_SECRET", "SESSION_SECRET"))
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, validation_alias=AliasChoices("STONEPI_DATA_DIR", "DATA_DIR"))
    auth_url: str = Field(default="http://127.0.0.1:8011", validation_alias=AliasChoices("STONEPI_AUTH_URL", "AUTH_URL"))
    notify_url: str = Field(default="http://127.0.0.1:8012", validation_alias=AliasChoices("STONEPI_NOTIFY_URL", "NOTIFY_URL"))

    # How a device request reached us. nginx sets X-StonePi-Path (lan | away) and overwrites
    # whatever the client sent; without it we assume the stricter path. run-dev sets "lan".
    default_path: str = Field(default="away", validation_alias=AliasChoices("AGENTLINK_DEFAULT_PATH"))
    # Hosts handed to devices. Blank lan_url: http://<this machine's LAN IP>[:lan_port].
    lan_url: str = Field(default="", validation_alias=AliasChoices("AGENTLINK_LAN_URL"))
    lan_port: int = Field(default=80, validation_alias=AliasChoices("AGENTLINK_LAN_PORT"))
    # Tailscale Funnel base (https://<name>.ts.net:8443). Blank: away use can't be offered yet.
    away_url: str = Field(default="", validation_alias=AliasChoices("AGENTLINK_AWAY_URL"))

    # Speech: "auto" uses faster-whisper + piper when installed; "dev" fakes both (run-dev).
    speech: str = Field(default="auto", validation_alias=AliasChoices("AGENTLINK_SPEECH"))
    whisper_model: str = Field(default="tiny.en", validation_alias=AliasChoices("AGENTLINK_WHISPER_MODEL"))
    whisper_hint: str = Field(default="", validation_alias=AliasChoices("AGENTLINK_WHISPER_HINT"))
    piper_bin: str = Field(default="piper", validation_alias=AliasChoices("AGENTLINK_PIPER_BIN"))
    piper_voice: str = Field(default="", validation_alias=AliasChoices("AGENTLINK_PIPER_VOICE"))


env = EnvSettings()
DATA_DIR = env.data_dir


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:  # noqa: BLE001 - no Vault (tests, dev) falls back to the env value
        pass
    return env.session_secret.strip()
