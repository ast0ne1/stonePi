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
    DEFAULT_TRUST_MIN_REVIEWS,
    DEFAULT_TRUST_REFRESH_DAYS,
    SCAN_CACHE_TTL_SECONDS,
)

_lock = threading.RLock()

WATCH_COLUMN_MIGRATIONS = (
    ("include_delivery", "INTEGER NOT NULL DEFAULT 0"),
    ("min_trust_score", "REAL"),
    ("require_rating", "INTEGER NOT NULL DEFAULT 0"),
    ("low_rated_mode", "TEXT NOT NULL DEFAULT 'ignore'"),
    ("low_rated_snapshot_json", "TEXT"),
    ("low_rated_fingerprint", "TEXT"),
    ("low_rated_at", "TEXT"),
)
LOW_RATED_MODES = ("ignore", "warn")


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

            CREATE TABLE IF NOT EXISTS merchants (
                source_id TEXT NOT NULL,
                merchant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                domain TEXT,
                domain_override TEXT,
                pr_rating REAL,
                pr_rating_count INTEGER,
                tp_score REAL,
                tp_review_count INTEGER,
                tp_url TEXT,
                tp_website TEXT,
                tp_status TEXT NOT NULL DEFAULT 'pending',
                tp_fetched_at TEXT,
                tp_last_error TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source_id, merchant_id)
            );

            CREATE INDEX IF NOT EXISTS idx_watches_status ON watches(status);
            CREATE INDEX IF NOT EXISTS idx_watches_next ON watches(next_check_at);
            CREATE INDEX IF NOT EXISTS idx_obs_watch ON observations(watch_id, checked_at);
            """
        )
        # Columns added after 0.0.6. Existing watches keep product-price targets
        # (include_delivery 0) and no minimum score, so nothing changes under them.
        existing = {str(r["name"]) for r in conn.execute("PRAGMA table_info(watches)").fetchall()}
        for column, ddl in WATCH_COLUMN_MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE watches ADD COLUMN {column} {ddl}")
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
            "trust_brightdata_enabled": "0",
            "trust_pricerunner_fallback": "0",
            "trust_min_reviews": str(DEFAULT_TRUST_MIN_REVIEWS),
            "trust_refresh_days": str(DEFAULT_TRUST_REFRESH_DAYS),
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


def _json_or_none(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _row_to_watch(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["strike"] = _json_or_none(data.get("strike_snapshot_json"))
    data["low_rated"] = _json_or_none(data.get("low_rated_snapshot_json"))
    data["in_stock_required"] = bool(data.get("in_stock_required"))
    data["include_delivery"] = bool(data.get("include_delivery"))
    data["require_rating"] = bool(data.get("require_rating"))
    if data.get("low_rated_mode") not in LOW_RATED_MODES:
        data["low_rated_mode"] = "ignore"
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
                in_stock_required, schedule_minutes, include_delivery, min_trust_score,
                require_rating, low_rated_mode, status, next_check_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'watching', ?, ?, ?)
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
                1 if payload.get("include_delivery", True) else 0,
                payload.get("min_trust_score"),
                1 if payload.get("require_rating") else 0,
                payload.get("low_rated_mode") if payload.get("low_rated_mode") in LOW_RATED_MODES else "ignore",
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
        "include_delivery",
        "min_trust_score",
        "require_rating",
        "low_rated_mode",
        "low_rated_snapshot_json",
        "low_rated_fingerprint",
        "low_rated_at",
    }
    sets: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"in_stock_required", "include_delivery", "require_rating"}:
            value = 1 if value else 0
        if key == "low_rated_mode" and value not in LOW_RATED_MODES:
            value = "ignore"
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
        low_rated_snapshot_json=None,
        low_rated_fingerprint=None,
        low_rated_at=None,
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


def latest_offers_by_watch() -> dict[int, list[dict[str, Any]]]:
    """Offers from each watch's most recent observation (dashboard rows)."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT o.watch_id, o.offers_json FROM observations o
            JOIN (SELECT watch_id, MAX(id) AS id FROM observations GROUP BY watch_id) latest
              ON latest.id = o.id
            """
        ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        offers = _json_or_none(row["offers_json"])
        out[int(row["watch_id"])] = offers if isinstance(offers, list) else []
    return out


# -- merchants (shops) -------------------------------------------------------------


def _row_to_merchant(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["effective_domain"] = data.get("domain_override") or data.get("domain")
    return data


def upsert_merchants(source_id: str, offers: list[dict[str, Any]]) -> int:
    """Record every shop seen in an offers fetch. Returns how many were new.

    Refreshes name, PriceRunner rating and discovered domain; a changed domain
    re-queues the Trustpilot lookup. Never touches the user's domain override.
    """
    now = _iso()
    seen: dict[str, dict[str, Any]] = {}
    for offer in offers:
        mid = str(offer.get("merchant_id") or "").strip()
        if mid and mid not in seen:
            seen[mid] = offer
    if not seen:
        return 0
    new = 0
    with db() as conn:
        for mid, offer in seen.items():
            row = conn.execute(
                "SELECT domain FROM merchants WHERE source_id = ? AND merchant_id = ?",
                (source_id, mid),
            ).fetchone()
            domain = offer.get("merchant_domain")
            if row is None:
                new += 1
                conn.execute(
                    """
                    INSERT INTO merchants (
                        source_id, merchant_id, name, domain, pr_rating, pr_rating_count,
                        tp_status, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        source_id,
                        mid,
                        str(offer.get("retailer") or "Retailer"),
                        domain,
                        offer.get("merchant_rating"),
                        offer.get("merchant_rating_count"),
                        now,
                        now,
                    ),
                )
                continue
            requeue = ""
            if domain and domain != row["domain"]:
                requeue = ", tp_status = CASE WHEN domain_override IS NULL THEN 'pending' ELSE tp_status END"
            conn.execute(
                "UPDATE merchants SET name = ?, pr_rating = ?, pr_rating_count = ?, last_seen_at = ?,"
                " domain = COALESCE(?, domain)" + requeue + " WHERE source_id = ? AND merchant_id = ?",
                (
                    str(offer.get("retailer") or "Retailer"),
                    offer.get("merchant_rating"),
                    offer.get("merchant_rating_count"),
                    now,
                    domain,
                    source_id,
                    mid,
                ),
            )
    return new


def get_merchants(source_id: str, merchant_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = sorted({str(m) for m in merchant_ids if m})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM merchants WHERE source_id = ? AND merchant_id IN ({marks})",
            (source_id, *ids),
        ).fetchall()
    return {str(r["merchant_id"]): _row_to_merchant(r) for r in rows}


def get_merchant(source_id: str, merchant_id: str) -> dict[str, Any] | None:
    return get_merchants(source_id, [merchant_id]).get(str(merchant_id))


def list_merchants() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM merchants ORDER BY last_seen_at DESC, name").fetchall()
    return [_row_to_merchant(r) for r in rows]


def set_merchant_domain_override(source_id: str, merchant_id: str, domain: str | None) -> None:
    """Save (or clear) the user's address correction and re-queue the score lookup."""
    with db() as conn:
        conn.execute(
            """
            UPDATE merchants SET domain_override = ?, tp_status = 'pending', tp_last_error = NULL
            WHERE source_id = ? AND merchant_id = ?
            """,
            (domain or None, source_id, merchant_id),
        )


def requeue_merchant(source_id: str, merchant_id: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE merchants SET tp_status = 'pending' WHERE source_id = ? AND merchant_id = ?",
            (source_id, merchant_id),
        )


MERCHANT_ERROR_RETRY_HOURS = 6


def merchants_due_for_score(*, refresh_days: int, seen_within_days: int = 60) -> list[dict[str, Any]]:
    """Shops that need a Trustpilot lookup: pending ones first, then stale ones still in use.

    A shop whose own lookup errored is retried after a few hours, not on the weekly cycle.
    """
    stale_before = _iso(_utc_now() - timedelta(days=max(1, refresh_days)))
    error_before = _iso(_utc_now() - timedelta(hours=MERCHANT_ERROR_RETRY_HOURS))
    seen_after = _iso(_utc_now() - timedelta(days=seen_within_days))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM merchants
            WHERE COALESCE(domain_override, domain) IS NOT NULL
              AND (
                tp_status = 'pending'
                OR (tp_status = 'error' AND (tp_fetched_at IS NULL OR tp_fetched_at < ?))
                OR (last_seen_at >= ? AND (tp_fetched_at IS NULL OR tp_fetched_at < ?))
              )
            ORDER BY CASE WHEN tp_status = 'pending' THEN 0 ELSE 1 END, last_seen_at DESC
            """,
            (error_before, seen_after, stale_before),
        ).fetchall()
    return [_row_to_merchant(r) for r in rows]


def update_merchant_score(
    source_id: str,
    merchant_id: str,
    *,
    status: str,
    score: float | None = None,
    review_count: int | None = None,
    url: str | None = None,
    website: str | None = None,
    error: str | None = None,
) -> None:
    """Store a lookup result. Errors keep the last known score."""
    with db() as conn:
        if status == "error":
            conn.execute(
                """
                UPDATE merchants SET tp_status = 'error', tp_last_error = ?, tp_fetched_at = ?
                WHERE source_id = ? AND merchant_id = ?
                """,
                ((error or "Lookup failed")[:300], _iso(), source_id, merchant_id),
            )
            return
        conn.execute(
            """
            UPDATE merchants SET
              tp_status = ?, tp_score = ?, tp_review_count = ?, tp_url = ?, tp_website = ?,
              tp_fetched_at = ?, tp_last_error = NULL
            WHERE source_id = ? AND merchant_id = ?
            """,
            (status, score, review_count, url, website, _iso(), source_id, merchant_id),
        )


def format_price(value: float | None, currency: str = "DKK") -> str:
    if value is None:
        return "—"
    if currency.upper() == "DKK":
        return f"{value:,.0f} DKK".replace(",", ".")
    return f"{value:,.2f} {currency}"
