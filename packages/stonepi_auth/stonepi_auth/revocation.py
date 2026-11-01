"""Shared ledger of revoked platform session ids.

Auth writes here when a session is dropped (logout, password change, disable).
Sibling apps call ``is_revoked`` from ``decode_session`` so a revoked cookie stops
working immediately instead of lasting until ``exp`` (~14 days).

Stored next to the Vault (same dir every app can read). Entries expire after the
cookie max age so the file cannot grow forever.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .session import COOKIE_MAX_AGE

_LOCK = threading.Lock()
_cache: dict[str, float] | None = None
_cache_mtime: float | None = None
_cache_checked_at = 0.0
_CACHE_TTL = 2.0


def _ledger_path() -> Path:
    configured = (os.environ.get("STONEPI_VAULT_DIR") or "").strip()
    root = Path(configured).expanduser() if configured else Path("/var/lib/stonepi/vault")
    return root / "revoked_sessions.json"


def _read_ledger() -> dict[str, float]:
    """Load the ledger. Missing file → empty. Unreadable/corrupt file raises (fail closed)."""
    path = _ledger_path()
    if not path.is_file():
        return {}
    raw_text = path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    sids = raw.get("sids") if isinstance(raw, dict) else None
    if not isinstance(sids, dict):
        raise ValueError("revocation ledger missing sids object")
    now = time.time()
    out: dict[str, float] = {}
    for sid, exp in sids.items():
        key = str(sid or "").strip()
        try:
            until = float(exp)
        except (TypeError, ValueError):
            continue
        if key and until > now:
            out[key] = until
    return out


def _write_ledger(sids: dict[str, float]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"sids": sids}, separators=(",", ":"), sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o660)
    except OSError:
        pass
    tmp.replace(path)


def _fresh_ledger() -> dict[str, float]:
    """Return the ledger, refreshing from disk at most every ``_CACHE_TTL`` seconds."""
    global _cache, _cache_mtime, _cache_checked_at
    now = time.time()
    path = _ledger_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    if (
        _cache is not None
        and now - _cache_checked_at < _CACHE_TTL
        and mtime == _cache_mtime
    ):
        return _cache
    with _LOCK:
        if (
            _cache is not None
            and now - _cache_checked_at < _CACHE_TTL
            and mtime == _cache_mtime
        ):
            return _cache
        _cache = _read_ledger()
        _cache_mtime = mtime
        _cache_checked_at = now
        return _cache


def record_revoked(session_id: str, *, max_age: int = COOKIE_MAX_AGE) -> None:
    """Mark a session id as revoked until roughly when its cookie would expire."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    until = time.time() + max(60, int(max_age))
    with _LOCK:
        try:
            sids = _read_ledger()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Unreadable/corrupt ledger: rebuild rather than lose the new revoke.
            sids = {}
        sids[sid] = until
        # Drop anything already past expiry while we're here.
        now = time.time()
        sids = {k: v for k, v in sids.items() if v > now}
        try:
            _write_ledger(sids)
        except OSError:
            return
        global _cache, _cache_mtime, _cache_checked_at
        _cache = sids
        try:
            _cache_mtime = _ledger_path().stat().st_mtime
        except OSError:
            _cache_mtime = None
        _cache_checked_at = time.time()


def is_revoked(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    return sid in _fresh_ledger()


def clear_for_tests() -> None:
    """Reset in-memory cache and delete the ledger file (tests only)."""
    global _cache, _cache_mtime, _cache_checked_at
    with _LOCK:
        _cache = None
        _cache_mtime = None
        _cache_checked_at = 0.0
        path = _ledger_path()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            path.with_name(path.name + ".tmp").unlink(missing_ok=True)
        except OSError:
            pass
