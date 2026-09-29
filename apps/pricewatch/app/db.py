from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import (
    DATA_DIR,
    DB_PATH,
    DEFAULT_CONDITION,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SCHEDULE_MINUTES,
    SCAN_CACHE_TTL_SECONDS,
)

_lock = threading.RLock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    value = dt or _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_ok_at TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT NOT NULL DEFAULT 'local',
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                variant TEXT,
                manufacturer TEXT,
                image_url TEXT,
                product_url TEXT,
                market TEXT NOT NULL DEFAULT 'DK',
                currency TEXT NOT NULL DEFAULT 'DKK',
                target_price REAL NOT NULL,
                condition TEXT NOT NULL DEFAULT 'new',
                in_stock_required INTEGER NOT NULL DEFAULT 1,
                schedule_minutes INTEGER NOT NULL DEFAULT 360,
                status TEXT NOT NULL DEFAULT 'watching',
                current_lowest REAL,
                last_checked_at TEXT,
                next_check_at TEXT,
                last_error TEXT,
                strike_snapshot_json TEXT,
                strike_fingerprint TEXT,
                strike_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL,
                lowest_price REAL,
                offer_count INTEGER NOT NULL DEFAULT 0,
                qualifying_count INTEGER NOT NULL DEFAULT 0,
                offers_json TEXT,
                checked_at TEXT NOT NULL,
                note TEXT,
                FOREIGN KEY (watch_id) REFERENCES watches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scan_cache (
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                offers_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (source_id, product_id)
            );

            CREATE INDEX IF NOT EXISTS idx_watches_status ON watches(status);
            CREATE INDEX IF NOT EXISTS idx_watches_next ON watches(next_check_at);
            CREATE INDEX IF NOT EXISTS idx_obs_watch ON observations(watch_id, checked_at);
            """
        )
        # Live default: PriceRunner only. Mock is registered only when PRICEWATCH_MOCK=1
        # (see ensure_mock_source) so it does not clutter Settings on a real Pi.
        conn.execute(
            """
            INSERT OR IGNORE INTO sources (id, name, enabled)
            VALUES ('pricerunner_dk', 'PriceRunner Denmark', 1)
            """
        )
        defaults = {
            "default_schedule_minutes": str(DEFAULT_SCHEDULE_MINUTES),
            "default_condition": DEFAULT_CONDITION,
            "default_in_stock_required": "1",
            "retention_days": str(DEFAULT_RETENTION_DAYS),
            "market": "DK",
            "currency": "DKK",
            "ntfy_enabled": "0",
            "ntfy_server": "https://ntfy.sh",
            "ntfy_topic": "",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def list_settings() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {str(r["key"]): str(r["value"]) for r in rows}


# Sources that may be deleted from Settings (not re-seeded on every boot).
REMOVABLE_SOURCE_IDS = frozenset({"mock"})


def list_sources(*, include_mock: bool | None = None) -> list[dict[str, Any]]:
    """List registered sources. Mock appears only if present in DB (and only seeded when PRICEWATCH_MOCK)."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.enabled, s.last_ok_at, s.last_error,
                   (SELECT COUNT(*) FROM watches w WHERE w.source_id = s.id) AS watch_count
            FROM sources s
            ORDER BY s.name
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        sid = str(item["id"])
        if include_mock is False and sid == "mock":
            continue
        item["removable"] = sid in REMOVABLE_SOURCE_IDS
        item["watch_count"] = int(item.get("watch_count") or 0)
        out.append(item)
    return out


def ensure_mock_source(*, enabled: bool = True) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, enabled)
            VALUES ('mock', 'Mock source', ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              enabled = excluded.enabled
            """,
            (1 if enabled else 0,),
        )


def count_watches_for_source(source_id: str) -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM watches WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def delete_source(source_id: str, *, cascade_watches: bool = True) -> tuple[bool, str]:
    """Remove a source row. Cascades watches (and their observations) by default."""
    sid = (source_id or "").strip()
    if sid not in REMOVABLE_SOURCE_IDS:
        return False, "That source cannot be removed."
    with db() as conn:
        watching = conn.execute(
            "SELECT COUNT(*) AS n FROM watches WHERE source_id = ?",
            (sid,),
        ).fetchone()
        n = int(watching["n"]) if watching else 0
        if n and not cascade_watches:
            return False, f"Remove or reassign {n} watch(es) that still use this source."
        if n:
            # observations cascade via FK; delete watches first
            conn.execute("DELETE FROM watches WHERE source_id = ?", (sid,))
        cur = conn.execute("DELETE FROM sources WHERE id = ?", (sid,))
        conn.execute("DELETE FROM scan_cache WHERE source_id = ?", (sid,))
        if cur.rowcount < 1:
            return False, "Source not found."
    return True, ""


def set_source_enabled(source_id: str, enabled: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE sources SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, source_id),
        )


def update_source_status(source_id: str, *, ok: bool, error: str | None = None) -> None:
    with db() as conn:
        if ok:
            conn.execute(
                "UPDATE sources SET last_ok_at = ?, last_error = NULL WHERE id = ?",
                (_iso(), source_id),
            )
        else:
            conn.execute(
                "UPDATE sources SET last_error = ? WHERE id = ?",
                (error or "Unknown error", source_id),
            )


def _row_to_watch(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    snap = data.get("strike_snapshot_json")
    if snap:
        try:
            data["strike"] = json.loads(snap)
        except json.JSONDecodeError:
            data["strike"] = None
    else:
        data["strike"] = None
    data["in_stock_required"] = bool(data.get("in_stock_required"))
    target = data.get("target_price")
    lowest = data.get("current_lowest")
    if target is not None and lowest is not None:
        data["distance"] = float(lowest) - float(target)
    else:
        data["distance"] = None
    return data


def list_watches(user_key: str | None = None) -> list[dict[str, Any]]:
    with db() as conn:
        if user_key:
            rows = conn.execute(
                """
                SELECT * FROM watches
                WHERE user_key = ?
                ORDER BY
                  CASE status
                    WHEN 'strike_found' THEN 0
                    WHEN 'error' THEN 1
                    WHEN 'watching' THEN 2
                    WHEN 'no_results' THEN 3
                    ELSE 4
                  END,
                  updated_at DESC
                """,
                (user_key,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM watches
                ORDER BY
                  CASE status
                    WHEN 'strike_found' THEN 0
                    WHEN 'error' THEN 1
                    WHEN 'watching' THEN 2
                    WHEN 'no_results' THEN 3
                    ELSE 4
                  END,
                  updated_at DESC
                """
            ).fetchall()
    return [_row_to_watch(r) for r in rows]


def get_watch(watch_id: int, user_key: str | None = None) -> dict[str, Any] | None:
    with db() as conn:
        if user_key:
            row = conn.execute(
                "SELECT * FROM watches WHERE id = ? AND user_key = ?",
                (watch_id, user_key),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return _row_to_watch(row) if row else None


def create_watch(payload: dict[str, Any]) -> int:
    now = _iso()
    schedule = int(payload.get("schedule_minutes") or DEFAULT_SCHEDULE_MINUTES)
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO watches (
                user_key, source_id, product_id, product_name, variant, manufacturer,
                image_url, product_url, market, currency, target_price, condition,
                in_stock_required, schedule_minutes, status, next_check_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'watching', ?, ?, ?)
            """,
            (
                payload.get("user_key") or "local",
                payload["source_id"],
                payload["product_id"],
                payload["product_name"],
                payload.get("variant"),
                payload.get("manufacturer"),
                payload.get("image_url"),
                payload.get("product_url"),
                payload.get("market") or "DK",
                payload.get("currency") or "DKK",
                float(payload["target_price"]),
                payload.get("condition") or DEFAULT_CONDITION,
                1 if payload.get("in_stock_required", True) else 0,
                schedule,
                now,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_watch_fields(watch_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "target_price",
        "condition",
        "in_stock_required",
        "schedule_minutes",
        "status",
        "current_lowest",
        "last_checked_at",
        "next_check_at",
        "last_error",
        "strike_snapshot_json",
        "strike_fingerprint",
        "strike_at",
        "variant",
        "image_url",
        "product_url",
        "product_name",
        "manufacturer",
    }
    sets: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "in_stock_required":
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(_iso())
    values.append(watch_id)
    with db() as conn:
        conn.execute(f"UPDATE watches SET {', '.join(sets)} WHERE id = ?", values)


def delete_watch(watch_id: int, user_key: str | None = None) -> bool:
    with db() as conn:
        if user_key:
            cur = conn.execute(
                "DELETE FROM watches WHERE id = ? AND user_key = ?",
                (watch_id, user_key),
            )
        else:
            cur = conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        return cur.rowcount > 0


def set_watch_status(watch_id: int, status: str, *, error: str | None = None) -> None:
    fields: dict[str, Any] = {"status": status}
    if error is not None:
        fields["last_error"] = error
    elif status != "error":
        fields["last_error"] = None
    update_watch_fields(watch_id, **fields)


def pause_watch(watch_id: int) -> None:
    set_watch_status(watch_id, "paused")


def resume_watch(watch_id: int) -> None:
    update_watch_fields(
        watch_id,
        status="watching",
        last_error=None,
        next_check_at=_iso(),
    )


def rearm_watch(watch_id: int) -> None:
    update_watch_fields(
        watch_id,
        status="watching",
        strike_snapshot_json=None,
        strike_fingerprint=None,
        strike_at=None,
        last_error=None,
        next_check_at=_iso(),
    )


def due_watches(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or _utc_now()
    now_s = _iso(now)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM watches
            WHERE status NOT IN ('paused')
              AND (next_check_at IS NULL OR next_check_at <= ?)
            ORDER BY CASE WHEN next_check_at IS NULL THEN 0 ELSE 1 END, next_check_at ASC
            """,
            (now_s,),
        ).fetchall()
    return [_row_to_watch(r) for r in rows]


def add_observation(
    watch_id: int,
    *,
    lowest_price: float | None,
    offer_count: int,
    qualifying_count: int,
    offers: list[dict[str, Any]] | None = None,
    note: str | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO observations (
                watch_id, lowest_price, offer_count, qualifying_count, offers_json, checked_at, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watch_id,
                lowest_price,
                offer_count,
                qualifying_count,
                json.dumps(offers or [], ensure_ascii=False),
                _iso(),
                note,
            ),
        )


def list_observations(watch_id: int, *, limit: int = 60) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM observations
            WHERE watch_id = ?
            ORDER BY checked_at DESC
            LIMIT ?
            """,
            (watch_id, limit),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw = item.get("offers_json")
        if raw:
            try:
                item["offers"] = json.loads(raw)
            except json.JSONDecodeError:
                item["offers"] = []
        else:
            item["offers"] = []
        out.append(item)
    return out


def historical_lowest(watch_id: int) -> float | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT MIN(lowest_price) AS m
            FROM observations
            WHERE watch_id = ? AND lowest_price IS NOT NULL
            """,
            (watch_id,),
        ).fetchone()
    if not row or row["m"] is None:
        return None
    return float(row["m"])


def get_scan_cache(source_id: str, product_id: str, *, ttl_seconds: int = SCAN_CACHE_TTL_SECONDS) -> list[dict[str, Any]] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT offers_json, fetched_at FROM scan_cache WHERE source_id = ? AND product_id = ?",
            (source_id, product_id),
        ).fetchone()
    if not row:
        return None
    fetched = _parse_iso(row["fetched_at"])
    if not fetched:
        return None
    if (_utc_now() - fetched).total_seconds() > ttl_seconds:
        return None
    try:
        data = json.loads(row["offers_json"])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def set_scan_cache(source_id: str, product_id: str, offers: list[dict[str, Any]]) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO scan_cache (source_id, product_id, offers_json, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, product_id) DO UPDATE SET
              offers_json = excluded.offers_json,
              fetched_at = excluded.fetched_at
            """,
            (source_id, product_id, json.dumps(offers, ensure_ascii=False), _iso()),
        )


def prune_observations(retention_days: int | None = None) -> int:
    days = retention_days
    if days is None:
        try:
            days = int(get_setting("retention_days", str(DEFAULT_RETENTION_DAYS)))
        except ValueError:
            days = DEFAULT_RETENTION_DAYS
    cutoff = _iso(_utc_now() - timedelta(days=max(1, days)))
    with db() as conn:
        cur = conn.execute("DELETE FROM observations WHERE checked_at < ?", (cutoff,))
        return int(cur.rowcount)


def watch_counts() -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM watches GROUP BY status"
        ).fetchall()
    counts = {status: 0 for status in ("watching", "strike_found", "paused", "error", "no_results")}
    total = 0
    for row in rows:
        counts[str(row["status"])] = int(row["n"])
        total += int(row["n"])
    counts["total"] = total
    counts["active"] = counts["watching"] + counts["strike_found"] + counts["no_results"] + counts["error"]
    return counts


def format_price(value: float | None, currency: str = "DKK") -> str:
    if value is None:
        return "—"
    if currency.upper() == "DKK":
        return f"{value:,.0f} DKK".replace(",", ".")
    return f"{value:,.2f} {currency}"
