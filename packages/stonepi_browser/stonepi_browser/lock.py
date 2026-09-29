"""Cross-app exclusive lock so only one headless Chromium runs at a time.

Uses an fcntl file lock on Linux (Pi). On Windows (dev) or when fcntl is
unavailable, falls back to a process-local threading lock (no-op across
processes — fine for single-app local runs).

Each app runs as its own service user. With fs.protected_regular (default on
Debian / Raspberry Pi OS), an O_CREAT open of a file another user created in a
world-writable sticky dir fails with EACCES even when the mode is 0666. So an
existing lock file is always opened *without* O_CREAT, and tmpfiles.d pre-creates
it owned by root (the dir owner, which the rule allows).
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_DEFAULT_LOCK = Path("/run/stonepi/chromium.lock")
_FALLBACK_LOCK = Path("/tmp/stonepi-chromium.lock")
_thread_lock = threading.Lock()


def _lock_path() -> Path:
    override = (os.environ.get("STONEPI_CHROMIUM_LOCK") or "").strip()
    if override:
        return Path(override)
    return _DEFAULT_LOCK


def _open_lock(path: Path) -> int:
    """Open the lock file for flock, creating it only if it does not exist yet."""
    try:
        # No O_CREAT (blocked on another user's file under protected_regular), and
        # read-only is enough for flock even if a sibling left the file 0644.
        return os.open(str(path), os.O_RDONLY)
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    # World-writable sticky dir so sibling service users can share the lock.
    try:
        os.chmod(path.parent, 0o1777)
    except OSError:
        pass
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        # Another app created it between our two opens.
        return os.open(str(path), os.O_RDONLY)
    try:
        os.fchmod(fd, 0o666)
    except (OSError, AttributeError):
        pass
    return fd


def _open_lock_with_fallback() -> int:
    path = _lock_path()
    try:
        return _open_lock(path)
    except OSError:
        if path == _FALLBACK_LOCK:
            raise
        return _open_lock(_FALLBACK_LOCK)


@contextmanager
def chromium_lock() -> Iterator[None]:
    """Hold the appliance-wide Chromium lock for the duration of the block."""
    try:
        import fcntl
    except ImportError:
        with _thread_lock:
            yield
        return

    with _thread_lock:
        fd = _open_lock_with_fallback()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
