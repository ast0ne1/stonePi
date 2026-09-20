from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _repo_data_exposure() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "exposure"


def exposure_file_path() -> Path:
    """Where Dashboard writes lan|public (hot flip, no service restart)."""
    explicit = os.environ.get("STONEPI_EXPOSURE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    if Path("/var/lib/stonepi").is_dir():
        return Path("/var/lib/stonepi/exposure")
    return _repo_data_exposure()


def _candidate_exposure_files() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in (
        Path(os.environ["STONEPI_EXPOSURE_FILE"]) if os.environ.get("STONEPI_EXPOSURE_FILE", "").strip() else None,
        Path("/var/lib/stonepi/exposure"),
        _repo_data_exposure(),
    ):
        if path is None or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _read_exposure_file() -> str:
    for path in _candidate_exposure_files():
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
                if value in {"lan", "public"}:
                    return value
        except OSError:
            continue
    return ""


def exposure_mode() -> str:
    """lan (default) or public.

    Prefer the on-disk flag (Dashboard can flip without restarting services),
    then STONEPI_EXPOSURE env (installer default: lan).
    """
    from_file = _read_exposure_file()
    if from_file:
        return from_file
    value = (os.environ.get("STONEPI_EXPOSURE") or "lan").strip().lower()
    return "public" if value == "public" else "lan"


def set_exposure_mode(mode: str) -> Path:
    """Write lan|public for all apps to pick up on next request."""
    cleaned = "public" if (mode or "").strip().lower() == "public" else "lan"
    path = exposure_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    return path


def is_public_exposure() -> bool:
    return exposure_mode() == "public"


def request_is_https(request: Any) -> bool:
    headers = getattr(request, "headers", None) or {}
    proto = ""
    if hasattr(headers, "get"):
        proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if not proto:
        url = getattr(request, "url", None)
        scheme = getattr(url, "scheme", None) or ""
        if not scheme and hasattr(request, "is_secure"):
            return bool(request.is_secure)
        proto = str(scheme).lower()
    return proto == "https"


def client_ip(request: Any, *, trusted_proxy: bool = True) -> str:
    """Client IP for rate limits.

    When behind StonePi nginx, prefer X-Real-IP (set to $remote_addr by nginx).
    Do not trust the leftmost X-Forwarded-For hop from the client.
    """
    headers = getattr(request, "headers", None) or {}
    get = headers.get if hasattr(headers, "get") else lambda *_a, **_k: None

    if trusted_proxy:
        real = (get("x-real-ip") or "").strip()
        if real:
            return real
        forwarded = (get("x-forwarded-for") or "").strip()
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[-1]

    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    if host:
        return str(host)
    remote = getattr(request, "remote_addr", None)
    if remote:
        return str(remote)
    return "127.0.0.1"


def _header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.title())
    return (value or "").strip()


def request_public_origin(request: Any) -> str:
    """Scheme://host the browser used (honours nginx X-Forwarded-*)."""
    proto = _header(request, "x-forwarded-proto").split(",")[0].strip()
    host = _header(request, "x-forwarded-host").split(",")[0].strip()
    if not host:
        host = _header(request, "host").split(",")[0].strip()
    if not proto:
        if request_is_https(request):
            proto = "https"
        else:
            url = getattr(request, "url", None)
            proto = str(getattr(url, "scheme", None) or getattr(request, "scheme", None) or "http")
    if not host:
        url = getattr(request, "url", None)
        host = str(getattr(url, "netloc", None) or getattr(request, "host", None) or "").strip()
    if not host:
        return ""
    return f"{proto}://{host}".rstrip("/")


def portal_home_url(request: Any, fallback: str = "") -> str:
    """Dashboard launcher URL for app nav / Back to apps.

    Returns an absolute URL so PrefixRewriter does not turn ``/`` into
    ``/auth/``, ``/news/``, etc. On path installs (nginx / Linux), prefer the
    request host (``.home`` / ``.local`` / IP) so Home stays on the hostname
    you opened. On Windows split-port solo-dev, each app has its own port —
    use the dashboard ``PUBLIC_ORIGIN`` fallback instead of the app's origin.
    """
    fb = (fallback or "").rstrip("/")
    # Windows run-dev: Studio is :8005, Dashboard is :8010 — request origin
    # would loop Home back into the app. Prefer the configured portal URL.
    if os.name == "nt" and fb:
        return fb + "/"
    origin = request_public_origin(request)
    if origin:
        return origin + "/"
    if fb:
        return fb + "/"
    return "/"


def browser_auth_url(configured: str, *, routing: str = "path") -> str:
    """Auth base URL for browser login/logout redirects.

    Path installs behind nginx must use relative ``/auth`` so Sign out stays on
    the hostname the user opened (``.home`` / ``.local`` / LAN IP). Keep absolute
    loopback ``AUTH_URL`` for Windows split-port solo-dev (no nginx ``/auth``).
    Server-side calls should keep using the configured loopback URL directly.
    """
    raw = (configured or "").strip() or "/auth"
    if Path("/etc/nginx/sites-enabled/stonepi").exists():
        return "/auth"
    if routing == "path" and os.name != "nt":
        return "/auth"
    return raw
