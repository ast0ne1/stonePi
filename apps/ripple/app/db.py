"""Ripple's SQLite store: posts, their photos and a few board settings (ideation/ripple.md).

One board for now; ``board_id`` is kept on every post so a second board needs no
migration. Times are UTC ISO strings. Deleting a post sets ``deleted_at`` and removes its
photo rows (the caller deletes the files). Schema changes go in MIGRATIONS and are
applied in order using ``PRAGMA user_version``.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from app.config import DATA_DIR

if TYPE_CHECKING:
    from app.photos import Photo

DB_FILE = "ripple.sqlite"
BOARD_ID = 1
BODY_MAX = 2000
BOARD_NAME_MAX = 40
DEFAULT_BOARD_NAME = "Family"

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE posts (
        id INTEGER PRIMARY KEY,
        board_id INTEGER NOT NULL DEFAULT 1,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        show_on_display INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    );
    CREATE INDEX posts_feed ON posts (board_id, deleted_at, created_at DESC);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE post_media (
        id INTEGER PRIMARY KEY,
        post_id INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
        storage_key TEXT NOT NULL UNIQUE,
        ext TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        caption TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX post_media_post ON post_media (post_id, position);
    """,
)

_lock = threading.Lock()


def db_path() -> Path:
    return DATA_DIR / DB_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            _migrate(conn)
            with conn:
                yield conn
        finally:
            conn.close()


def _media_rows(conn: sqlite3.Connection, post_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    by_post: dict[int, list[dict[str, Any]]] = {pid: [] for pid in post_ids}
    if not post_ids:
        return by_post
    marks = ",".join("?" * len(post_ids))
    rows = conn.execute(
        f"SELECT * FROM post_media WHERE post_id IN ({marks}) ORDER BY post_id, position, id", post_ids
    ).fetchall()
    for row in rows:
        by_post[row["post_id"]].append(dict(row))
    return by_post


def _with_media(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    posts = []
    for row in rows:
        post = dict(row)
        post["show_on_display"] = bool(post["show_on_display"])
        posts.append(post)
    media = _media_rows(conn, [p["id"] for p in posts])
    for post in posts:
        post["media"] = media[post["id"]]
    return posts


def list_posts(page: int = 1, per_page: int = 20) -> tuple[list[dict[str, Any]], bool]:
    """(posts, has_more): newest first, deleted ones left out, each with its ``media``."""
    page = max(1, page)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE board_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (BOARD_ID, per_page + 1, (page - 1) * per_page),
        ).fetchall()
        posts = _with_media(conn, rows[:per_page])
    return posts, len(rows) > per_page


def display_posts(limit: int) -> list[dict[str, Any]]:
    """The newest posts marked for the family display, each with its ``media``."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE board_id = ? AND deleted_at IS NULL AND show_on_display = 1 "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (BOARD_ID, limit),
        ).fetchall()
        return _with_media(conn, rows)


def get_post(post_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM posts WHERE id = ? AND board_id = ? AND deleted_at IS NULL", (post_id, BOARD_ID)
        ).fetchone()
        if row is None:
            return None
        return _with_media(conn, [row])[0]


def _insert_media(conn: sqlite3.Connection, post_id: int, photos: list[Photo], start: int, stamp: str) -> None:
    conn.executemany(
        "INSERT INTO post_media (post_id, storage_key, ext, mime_type, file_size, width, height, caption, position, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (post_id, p.key, p.ext, p.mime_type, p.file_size, p.width, p.height, p.caption, start + n, stamp)
            for n, p in enumerate(photos)
        ],
    )


def create_post(
    *, author_id: str, author_name: str, body: str, show_on_display: bool, photos: list[Photo] | None = None
) -> dict[str, Any]:
    stamp = _now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO posts (board_id, author_id, author_name, body, show_on_display, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (BOARD_ID, author_id, author_name, body, int(show_on_display), stamp, stamp),
        )
        _insert_media(conn, cur.lastrowid, photos or [], 0, stamp)
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _with_media(conn, [row])[0]


def update_post(
    post_id: int,
    *,
    body: str,
    show_on_display: bool,
    captions: dict[int, str] | None = None,
    remove: set[int] | None = None,
    add: list[Photo] | None = None,
) -> list[tuple[str, str]] | None:
    """Save an edit in one transaction. Returns the (key, ext) of removed photos, or None if the post is gone."""
    stamp = _now()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE posts SET body = ?, show_on_display = ?, updated_at = ? "
            "WHERE id = ? AND board_id = ? AND deleted_at IS NULL",
            (body, int(show_on_display), stamp, post_id, BOARD_ID),
        )
        if cur.rowcount != 1:
            return None
        removed: list[tuple[str, str]] = []
        for media_id in remove or ():
            row = conn.execute(
                "SELECT storage_key, ext FROM post_media WHERE id = ? AND post_id = ?", (media_id, post_id)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM post_media WHERE id = ?", (media_id,))
                removed.append((row["storage_key"], row["ext"]))
        for media_id, caption in (captions or {}).items():
            conn.execute("UPDATE post_media SET caption = ? WHERE id = ? AND post_id = ?", (caption, media_id, post_id))
        if add:
            last = conn.execute("SELECT COALESCE(MAX(position), -1) FROM post_media WHERE post_id = ?", (post_id,)).fetchone()[0]
            _insert_media(conn, post_id, add, last + 1, stamp)
    return removed


def delete_post(post_id: int) -> list[tuple[str, str]] | None:
    """Hide the post and drop its photo rows. Returns their (key, ext), or None if already gone."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ? AND board_id = ? AND deleted_at IS NULL",
            (_now(), post_id, BOARD_ID),
        )
        if cur.rowcount != 1:
            return None
        rows = conn.execute("SELECT storage_key, ext FROM post_media WHERE post_id = ?", (post_id,)).fetchall()
        conn.execute("DELETE FROM post_media WHERE post_id = ?", (post_id,))
    return [(r["storage_key"], r["ext"]) for r in rows]


def media_by_key(key: str) -> dict[str, Any] | None:
    """A photo of a live post on this board."""
    with connect() as conn:
        row = conn.execute(
            "SELECT m.* FROM post_media m JOIN posts p ON p.id = m.post_id "
            "WHERE m.storage_key = ? AND p.board_id = ? AND p.deleted_at IS NULL",
            (key, BOARD_ID),
        ).fetchone()
    return dict(row) if row else None


def media_keys() -> set[str]:
    with connect() as conn:
        return {r[0] for r in conn.execute("SELECT storage_key FROM post_media")}


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def board_name() -> str:
    return get_setting("board_name", DEFAULT_BOARD_NAME) or DEFAULT_BOARD_NAME
