"""Lock an app when a platform install has no session secret.

Apps run "solo" (no accounts) when there is no session secret, which is right for a
standalone run but must never happen on a StonePi install: there a missing or unreadable
secret would open every page to anyone. A platform install is recognised by /etc/stonepi.
Dev runs (Windows run-dev, or STONEPI_DEV=1) are never locked.

    from stonepi_auth.platform_lock import add_platform_lock
    platform_lock = add_platform_lock(app, lambda: routes._session_secret())

The answer is cached for a few seconds so the secret (a Vault read) isn't looked up on
every request.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

PLATFORM_DIR = Path("/etc/stonepi")
LOCKED_MESSAGE = "Sign-in is unavailable: the StonePi session secret is missing. Re-run the installer."


def dev_mode() -> bool:
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


class PlatformLock:
    def __init__(
        self,
        secret_getter: Callable[[], str],
        *,
        is_dev: Callable[[], bool] = dev_mode,
        platform_dir: Path = PLATFORM_DIR,
        ttl: float = 5.0,
    ) -> None:
        self.secret_getter = secret_getter
        self.is_dev = is_dev
        self.platform_dir = platform_dir
        self.ttl = ttl
        self._checked_at = 0.0
        self._locked = False

    def locked(self) -> bool:
        now = time.monotonic()
        if self.ttl > 0 and self._checked_at and now - self._checked_at < self.ttl:
            return self._locked
        if self.is_dev() or not self.platform_dir.is_dir():
            locked = False
        else:
            try:
                locked = not (self.secret_getter() or "").strip()
            except Exception:  # noqa: BLE001 - an unreadable secret counts as missing
                locked = True
        self._locked, self._checked_at = locked, now
        return locked

    def clear(self) -> None:
        self._checked_at = 0.0

    @staticmethod
    def exempt(path: str) -> bool:
        return path.endswith("/healthz") or "/static/" in path


def add_platform_lock(app: Any, secret_getter: Callable[[], str], **kwargs: Any) -> PlatformLock:
    """Register the lock on a FastAPI/Starlette ``app``; returns it (tests adjust it)."""
    from starlette.responses import PlainTextResponse

    lock = PlatformLock(secret_getter, **kwargs)

    @app.middleware("http")
    async def _lock_without_session_secret(request, call_next):
        if not lock.exempt(request.url.path) and lock.locked():
            return PlainTextResponse(LOCKED_MESSAGE, status_code=503)
        return await call_next(request)

    return lock
