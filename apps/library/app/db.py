from __future__ import annotations

import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Any

from app.config import DATA_DIR, DB_PATH

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT NOT NULL,
    name TEXT NOT NULL,
    flavour TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    issued TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'installed',
    installed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    flavour TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    issued TEXT NOT NULL DEFAULT '',
    book_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    file_name TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    replaces_id INTEGER,
    in_place INTEGER NOT NULL DEFAULT 0,
    cancel INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

JOB_ACTIVE = ("queued", "downloading", "verifying", "installing")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    """One short-lived connection: commit on success, always closed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            yield conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


def _rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _one(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    rows = _rows(sql, args)
    return rows[0] if rows else None


def _exec(sql: str, args: tuple = ()) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(sql, args)
        return int(cur.lastrowid or 0)


# ---------- settings ----------

def get_setting(key: str, default: str = "") -> str:
    row = _one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    _exec(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------- content ----------

def list_content() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM content ORDER BY title, flavour")


def get_content(content_id: int) -> dict[str, Any] | None:
    return _one("SELECT * FROM content WHERE id = ?", (content_id,))


def add_content(row: dict[str, Any]) -> int:
    cols = ("book_id", "name", "flavour", "title", "summary", "language", "issued", "file_name", "path", "size", "sha256", "status")
    return _exec(
        f"INSERT INTO content ({', '.join(cols)}, installed_at) VALUES ({', '.join('?' for _ in cols)}, ?)",
        tuple(row.get(c, "" if c not in {"size"} else 0) for c in cols) + (now(),),
    )


def update_content(content_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE content SET {sets} WHERE id = ?", tuple(fields.values()) + (content_id,))


def delete_content(content_id: int) -> None:
    _exec("DELETE FROM content WHERE id = ?", (content_id,))


# ---------- jobs ----------

def add_job(row: dict[str, Any]) -> int:
    cols = ("kind", "name", "flavour", "title", "summary", "language", "issued", "book_id", "url", "file_name", "total", "sha256", "replaces_id", "in_place")
    stamp = now()
    return _exec(
        f"INSERT INTO jobs ({', '.join(cols)}, status, created_at, updated_at) VALUES ({', '.join('?' for _ in cols)}, 'queued', ?, ?)",
        tuple(row.get(c) if row.get(c) is not None else (0 if c in {"total", "in_place"} else ("" if c != "replaces_id" else None)) for c in cols)
        + (stamp, stamp),
    )


def get_job(job_id: int) -> dict[str, Any] | None:
    return _one("SELECT * FROM jobs WHERE id = ?", (job_id,))


def update_job(job_id: int, **fields: Any) -> None:
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE jobs SET {sets} WHERE id = ?", tuple(fields.values()) + (job_id,))


def active_jobs() -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in JOB_ACTIVE)
    return _rows(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id", JOB_ACTIVE)


def next_queued_job() -> dict[str, Any] | None:
    # Interrupted work (service restart) resumes first, then the queue in order.
    return _one(
        "SELECT * FROM jobs WHERE status IN ('downloading', 'verifying', 'installing', 'queued') "
        "ORDER BY CASE status WHEN 'queued' THEN 1 ELSE 0 END, id LIMIT 1"
    )


def recent_jobs(limit: int = 10) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))


def job_for_name(name: str, flavour: str) -> dict[str, Any] | None:
    marks = ", ".join("?" for _ in JOB_ACTIVE)
    return _one(
        f"SELECT * FROM jobs WHERE name = ? AND flavour = ? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
        (name, flavour, *JOB_ACTIVE),
    )
