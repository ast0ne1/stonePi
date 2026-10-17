"""Shared JSON files in the Vault folder, locked and replaced atomically (as stonepi_vault.quota).

Every service user is in group stonepi-vault and the folder is setgid (2770), so any app
can read and update them; files are kept 660 in that group.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Any

try:  # POSIX (the Pi); Windows dev has a single writer.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

_thread_lock = threading.Lock()


def ai_root() -> Path:
    """The Vault folder, or ``STONEPI_AI_DIR`` (tests, an app without a Vault)."""
    override = (os.environ.get("STONEPI_AI_DIR") or "").strip()
    if override:
        return Path(override)
    from stonepi_vault import get_vault

    return get_vault().root


def _secure_file(path: Path) -> None:
    try:
        os.chmod(path, 0o660)
    except OSError:
        pass
    if hasattr(os, "chown"):
        try:
            os.chown(path, -1, path.parent.stat().st_gid)
        except OSError:
            pass


@contextlib.contextmanager
def locked(path: Path):
    with _thread_lock:
        if fcntl is None:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.stem}.lock")
        handle = open(lock_path, "a+")
        try:
            _secure_file(lock_path)
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()


def read_json(path: Path) -> dict[str, Any] | None:
    """The file's object, or None when it's missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _secure_file(tmp)
    os.replace(tmp, path)
