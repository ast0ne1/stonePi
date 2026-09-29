from __future__ import annotations

import os
import re
import time
from pathlib import Path

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_CACHE_TTL_SECONDS = 30.0
_hostname_cache: str | None = None
_hostname_cache_at: float = 0.0


def _repo_data_hostname() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "hostname"


def hostname_file_path() -> Path:
    """Where Dashboard / helper writes the appliance LAN hostname (hot read)."""
    explicit = os.environ.get("STONEPI_HOSTNAME_FILE", "").strip()
    if explicit:
        return Path(explicit)
    if Path("/var/lib/stonepi").is_dir():
        return Path("/var/lib/stonepi/hostname")
    return _repo_data_hostname()


def _candidate_hostname_files() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in (
        Path(os.environ["STONEPI_HOSTNAME_FILE"]) if os.environ.get("STONEPI_HOSTNAME_FILE", "").strip() else None,
        Path("/var/lib/stonepi/hostname"),
        _repo_data_hostname(),
    ):
        if path is None or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def normalize_hostname(raw: str) -> str:
    value = (raw or "").strip().lower().removesuffix(".local").rstrip(".")
    return value


def valid_hostname(name: str) -> bool:
    return bool(name) and HOSTNAME_RE.fullmatch(name) is not None


def _read_hostname_file() -> str:
    for path in _candidate_hostname_files():
        try:
            if path.is_file():
                value = normalize_hostname(path.read_text(encoding="utf-8").strip().splitlines()[0])
                if valid_hostname(value):
                    return value
        except OSError:
            continue
    return ""


def _invalidate_hostname_cache() -> None:
    global _hostname_cache, _hostname_cache_at
    _hostname_cache = None
    _hostname_cache_at = 0.0


def platform_hostname() -> str:
    """Appliance short name for NAME.local / share URLs.

    Prefer the on-disk flag (Dashboard can rename without restarting services),
    then STONEPI_HOSTNAME env (installer default: stonepi).
    Cached for 30s to avoid repeated disk reads on hot request paths.
    """
    global _hostname_cache, _hostname_cache_at
    now = time.monotonic()
    if _hostname_cache is not None and (now - _hostname_cache_at) < _CACHE_TTL_SECONDS:
        return _hostname_cache
    from_file = _read_hostname_file()
    if from_file:
        value = from_file
    else:
        value = normalize_hostname(os.environ.get("STONEPI_HOSTNAME") or "stonepi")
        if not valid_hostname(value):
            value = "stonepi"
    _hostname_cache = value
    _hostname_cache_at = now
    return value


def set_platform_hostname(name: str) -> Path:
    """Write the appliance hostname for all apps to pick up on next read."""
    cleaned = normalize_hostname(name)
    if not valid_hostname(cleaned):
        raise ValueError("invalid hostname")
    path = hostname_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    _invalidate_hostname_cache()
    return path
