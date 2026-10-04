"""Platform glue shared by routes and services: session secret, prefix, events."""
from __future__ import annotations

import logging
import os

from stonepi_auth.config import PlatformSettings

from app.config import env

logger = logging.getLogger("library.platform")
APP_ID = "library"


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def auth_optional() -> bool:
    """Running without a session secret skips sign-in only in dev.

    Windows run-dev, or STONEPI_DEV=1. On a Pi a missing/unreadable secret must lock the
    Library (and the Kiwix reader behind /_auth) rather than open it to everyone.
    """
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


def prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    return PlatformSettings(
        enabled=bool(session_secret()),
        session_secret=session_secret(),
        app_id=APP_ID,
        prefix=prefix(),
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def emit(kind: str, title: str, summary: str, *, audience: str = "admin", severity: str = "info", dedupe: str = "", url: str | None = None) -> bool:
    """Send a ``library.{kind}`` event to Notify (platform only)."""
    if not session_secret():
        return False
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id=f"library.{kind}",
                source=APP_ID,
                title=title[:200],
                summary=summary[:500],
                severity=severity,
                audience=audience,
                dedupe_key=dedupe or f"library:{kind}",
                url=url or f"{prefix()}/",
            )
        )
    except Exception:
        logger.debug("emit_event library.%s failed", kind, exc_info=True)
        return False
