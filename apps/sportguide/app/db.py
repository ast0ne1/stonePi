from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.config import DATA_DIR, DB_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              last_ok_at TEXT,
              last_error TEXT,
              listing_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS listings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              external_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              sport TEXT NOT NULL,
              league TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              starts_at TEXT NOT NULL,
              ends_at TEXT,
              channels_json TEXT NOT NULL DEFAULT '[]',
              source_url TEXT,
              seen_at TEXT NOT NULL,
              UNIQUE(source_id, external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_listings_starts ON listings(starts_at);
            CREATE INDEX IF NOT EXISTS idx_listings_sport ON listings(sport);
            CREATE TABLE IF NOT EXISTS prefs (
              user_key TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY (user_key, key)
            );
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        for sid, name in (
            ("ausportguide", "AusSportGuide"),
            ("wheresthematch", "WheresTheMatch"),
        ):
            conn.execute(
                """
                INSERT INTO sources (id, name, enabled) VALUES (?, ?, 1)
                ON CONFLICT(id) DO NOTHING
                """,
                (sid, name),
            )


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pref(user_key: str, key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM prefs WHERE user_key = ? AND key = ?",
            (user_key, key),
        ).fetchone()
    return str(row["value"]) if row else default


def set_pref(user_key: str, key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO prefs (user_key, key, value) VALUES (?, ?, ?)
            ON CONFLICT(user_key, key) DO UPDATE SET value = excluded.value
            """,
            (user_key, key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_meta(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def list_sources() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def update_source_status(source_id: str, *, ok: bool, error: str | None = None, count: int = 0) -> None:
    with db() as conn:
        if ok:
            conn.execute(
                """
                UPDATE sources SET last_ok_at = ?, last_error = NULL, listing_count = ? WHERE id = ?
                """,
                (_utc_now(), count, source_id),
            )
        else:
            conn.execute(
                """
                UPDATE sources SET last_error = ?, listing_count = ? WHERE id = ?
                """,
                (error or "error", count, source_id),
            )


def replace_source_listings(source_id: str, rows: list[dict[str, Any]]) -> int:
    now = _utc_now()
    with db() as conn:
        conn.execute("DELETE FROM listings WHERE source_id = ?", (source_id,))
        for row in rows:
            channels = row.get("channels") or []
            if isinstance(channels, str):
                channels_json = channels
            else:
                channels_json = json.dumps(list(channels), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO listings (
                  external_id, source_id, sport, league, title,
                  starts_at, ends_at, channels_json, source_url, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["external_id"],
                    source_id,
                    row["sport"],
                    row.get("league") or "",
                    row["title"],
                    row["starts_at"],
                    row.get("ends_at"),
                    channels_json,
                    row.get("source_url"),
                    now,
                ),
            )
        conn.execute(
            "UPDATE sources SET last_ok_at = ?, last_error = NULL, listing_count = ? WHERE id = ?",
            (now, len(rows), source_id),
        )
    return len(rows)


def _row_to_listing(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    try:
        out["channels"] = json.loads(out.get("channels_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["channels"] = []
    return out


def query_listings(
    *,
    sport: str | None = None,
    league: str | None = None,
    starts_from: str | None = None,
    starts_to: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if sport and sport != "all":
        clauses.append("sport = ?")
        params.append(sport)
    if league and league != "all":
        if league == "Other":
            clauses.append(
                "sport = 'football' AND league NOT IN ("
                "'Premier League','La Liga','Serie A','Bundesliga','Ligue 1',"
                "'Champions League','Europa League','Conference League')"
            )
        else:
            clauses.append("league = ?")
            params.append(league)
    if starts_from:
        clauses.append("starts_at >= ?")
        params.append(starts_from)
    if starts_to:
        clauses.append("starts_at <= ?")
        params.append(starts_to)
    params.append(limit)
    sql = f"""
        SELECT * FROM listings
        WHERE {' AND '.join(clauses)}
        ORDER BY starts_at ASC
        LIMIT ?
    """
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_listing(r) for r in rows]


def count_on_now(starts_from: str, starts_to: str) -> int:
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM listings
            WHERE starts_at >= ? AND starts_at <= ?
            """,
            (starts_from, starts_to),
        ).fetchone()
    return int(row["n"] if row else 0)


def listing_count() -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM listings").fetchone()
    return int(row["n"] if row else 0)
