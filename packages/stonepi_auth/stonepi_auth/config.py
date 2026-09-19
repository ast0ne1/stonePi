from __future__ import annotations

import os
from dataclasses import dataclass


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return default if value is None else value


@dataclass(frozen=True)
class PlatformSettings:
    enabled: bool
    session_secret: str
    app_id: str
    prefix: str
    auth_url: str
    public_origin: str
    hostname: str

    @property
    def prefix_slash(self) -> str:
        prefix = self.prefix.strip()
        if not prefix or prefix == "/":
            return ""
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return prefix.rstrip("/")


def load_settings(*, app_id: str = "") -> PlatformSettings:
    secret = _env("STONEPI_SESSION_SECRET").strip()
    auth_url = _env("STONEPI_AUTH_URL", "").strip().rstrip("/")
    prefix = _env("STONEPI_PREFIX", "").strip()
    hostname = _env("STONEPI_HOSTNAME", "stonepi").strip() or "stonepi"
    origin = _env("STONEPI_PUBLIC_ORIGIN", "").strip().rstrip("/")
    enabled = bool(secret) or _env("STONEPI_AUTH", "").strip() in {"1", "true", "yes"}
    return PlatformSettings(
        enabled=enabled and bool(secret),
        session_secret=secret,
        app_id=app_id or _env("STONEPI_APP_ID", "").strip(),
        prefix=prefix,
        auth_url=auth_url,
        public_origin=origin,
        hostname=hostname,
    )
