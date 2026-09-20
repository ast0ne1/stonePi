from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import CACHE_DIR, CATEGORIES, DATA_DIR, DB_PATH, STORES

_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              dealer_id TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              last_ok_at TEXT,
              last_error TEXT,
              offer_count INTEGER NOT NULL DEFAULT 0,
              collector TEXT NOT NULL DEFAULT 'tjek'
            );
            CREATE TABLE IF NOT EXISTS products (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              key TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT 'Other'
            );
            CREATE TABLE IF NOT EXISTS offers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              external_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              product_id INTEGER NOT NULL,
              title TEXT NOT NULL,
              price_dkk REAL NOT NULL,
              unit_text TEXT,
              valid_from TEXT,
              valid_to TEXT,
              image_url TEXT,
              offer_url TEXT,
              catalog_url TEXT,
              currency TEXT NOT NULL DEFAULT 'DKK',
              is_hot INTEGER NOT NULL DEFAULT 0,
              seen_at TEXT NOT NULL,
              raw_json TEXT,
              UNIQUE(source_id, external_id),
              FOREIGN KEY(source_id) REFERENCES sources(id),
              FOREIGN KEY(product_id) REFERENCES products(id)
            );
            CREATE TABLE IF NOT EXISTS price_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              product_id INTEGER NOT NULL,
              source_id TEXT NOT NULL,
              price_dkk REAL NOT NULL,
              captured_at TEXT NOT NULL,
              FOREIGN KEY(product_id) REFERENCES products(id),
              FOREIGN KEY(source_id) REFERENCES sources(id)
            );
            CREATE TABLE IF NOT EXISTS list_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_key TEXT NOT NULL,
              label TEXT NOT NULL,
              product_id INTEGER,
              checked INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prefs (
              user_key TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY(user_key, key)
            );
            CREATE TABLE IF NOT EXISTS search_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_key TEXT NOT NULL,
              query TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_offers_source ON offers(source_id);
            CREATE INDEX IF NOT EXISTS idx_offers_title ON offers(title);
            CREATE INDEX IF NOT EXISTS idx_list_user ON list_items(user_key);
            """
        )
        # Upgrade older DBs that predate offer/catalog URL columns
        cols = {r[1] for r in conn.execute("PRAGMA table_info(offers)").fetchall()}
        for col, decl in (
            ("offer_url", "TEXT"),
            ("catalog_url", "TEXT"),
            ("currency", "TEXT NOT NULL DEFAULT 'DKK'"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE offers ADD COLUMN {col} {decl}")
        for store in STORES:
            conn.execute(
                """
                INSERT OR IGNORE INTO sources (id, name, dealer_id, enabled, collector)
                VALUES (?, ?, ?, 1, 'tjek')
                """,
                (store["id"], store["name"], store["dealer_id"]),
            )
        # Optional food-waste pseudo-sources (disabled until token+zip configured)
        for store_id, name in (("netto_fw", "Netto madspild"), ("foetex_fw", "føtex madspild")):
            conn.execute(
                """
                INSERT OR IGNORE INTO sources (id, name, dealer_id, enabled, collector)
                VALUES (?, ?, '', 0, 'salling_fw')
                """,
                (store_id, name),
            )


def seed_mock_if_empty() -> None:
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM offers").fetchone()["c"]
        if count:
            return
        seed_path = Path(__file__).resolve().parent / "data" / "mock_offers.json"
        if not seed_path.exists():
            return
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        now = _utc_now()
        for row in payload:
            product_id = _ensure_product(conn, row["title"], row.get("category") or "Other")
            conn.execute(
                """
                INSERT OR REPLACE INTO offers
                (external_id, source_id, product_id, title, price_dkk, unit_text,
                 valid_from, valid_to, image_url, offer_url, catalog_url, currency,
                 is_hot, seen_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["external_id"],
                    row["source_id"],
                    product_id,
                    row["title"],
                    float(row["price_dkk"]),
                    row.get("unit_text"),
                    row.get("valid_from"),
                    row.get("valid_to"),
                    row.get("image_url"),
                    row.get("offer_url"),
                    row.get("catalog_url"),
                    row.get("currency") or "DKK",
                    1 if row.get("is_hot") else 0,
                    now,
                    json.dumps(row),
                ),
            )
        for store in STORES:
            c = conn.execute(
                "SELECT COUNT(*) AS c FROM offers WHERE source_id = ?", (store["id"],)
            ).fetchone()["c"]
            conn.execute(
                "UPDATE sources SET last_ok_at = ?, last_error = NULL, offer_count = ? WHERE id = ?",
                (now, c, store["id"]),
            )


def _ensure_product(conn: sqlite3.Connection, title: str, category: str) -> int:
    key = " ".join(title.lower().split())
    row = conn.execute("SELECT id FROM products WHERE key = ?", (key,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO products (key, title, category) VALUES (?, ?, ?)",
        (key, title, category if category in CATEGORIES else "Other"),
    )
    return int(cur.lastrowid)


def list_sources(*, include_foodwaste: bool = True) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY name COLLATE NOCASE").fetchall()
    out = [dict(r) for r in rows]
    if not include_foodwaste:
        out = [r for r in out if r.get("collector") != "salling_fw"]
    return out


def set_source_enabled(source_id: str, enabled: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE sources SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, source_id),
        )


def update_source_status(
    source_id: str,
    *,
    ok: bool,
    offer_count: int = 0,
    error: str | None = None,
) -> None:
    with db() as conn:
        if ok:
            conn.execute(
                """
                UPDATE sources
                SET last_ok_at = ?, last_error = NULL, offer_count = ?
                WHERE id = ?
                """,
                (_utc_now(), offer_count, source_id),
            )
        else:
            conn.execute(
                "UPDATE sources SET last_error = ? WHERE id = ?",
                ((error or "refresh failed")[:500], source_id),
            )


def replace_source_offers(source_id: str, rows: list[dict[str, Any]]) -> int:
    now = _utc_now()
    with db() as conn:
        conn.execute("DELETE FROM offers WHERE source_id = ?", (source_id,))
        for row in rows:
            product_id = _ensure_product(conn, row["title"], row.get("category") or "Other")
            conn.execute(
                """
                INSERT INTO offers
                (external_id, source_id, product_id, title, price_dkk, unit_text,
                 valid_from, valid_to, image_url, offer_url, catalog_url, currency,
                 is_hot, seen_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["external_id"],
                    source_id,
                    product_id,
                    row["title"],
                    float(row["price_dkk"]),
                    row.get("unit_text"),
                    row.get("valid_from"),
                    row.get("valid_to"),
                    row.get("image_url"),
                    row.get("offer_url"),
                    row.get("catalog_url"),
                    row.get("currency") or "DKK",
                    1 if row.get("is_hot") else 0,
                    now,
                    json.dumps(row.get("raw") or row, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO price_history (product_id, source_id, price_dkk, captured_at)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, source_id, float(row["price_dkk"]), now),
            )
        conn.execute(
            """
            UPDATE sources
            SET last_ok_at = ?, last_error = NULL, offer_count = ?
            WHERE id = ?
            """,
            (now, len(rows), source_id),
        )
    return len(rows)


def _enrich_offer_row(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure public leaflet links exist (backfill from raw_json when columns empty)."""
    from app.money import offer_public_urls

    out = dict(row)
    raw: dict[str, Any] = {}
    if out.get("raw_json"):
        try:
            parsed = json.loads(out["raw_json"])
            if isinstance(parsed, dict):
                raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else parsed
        except (TypeError, json.JSONDecodeError):
            raw = {}
    urls = offer_public_urls(out.get("external_id"), raw)
    if not out.get("offer_url"):
        out["offer_url"] = urls.get("offer_url")
    if not out.get("catalog_url"):
        out["catalog_url"] = urls.get("catalog_url")
    if not out.get("currency"):
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        out["currency"] = str(pricing.get("currency") or "DKK").upper()
    return out


def query_offers(
    *,
    source_ids: list[str] | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    clauses = ["s.enabled = 1"]
    params: list[Any] = []
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        clauses.append(f"o.source_id IN ({placeholders})")
        params.extend(source_ids)
    if category and category != "All":
        clauses.append("p.category = ?")
        params.append(category)
    if q and q.strip():
        clauses.append("(lower(o.title) LIKE ? OR lower(p.title) LIKE ?)")
        like = f"%{q.strip().lower()}%"
        params.extend([like, like])
    where = " AND ".join(clauses)
    params.append(limit)
    sql = f"""
      SELECT o.*, s.name AS source_name, p.category AS category
      FROM offers o
      JOIN sources s ON s.id = o.source_id
      JOIN products p ON p.id = o.product_id
      WHERE {where}
      ORDER BY o.is_hot DESC, o.seen_at DESC, o.price_dkk ASC
      LIMIT ?
    """
    with db() as conn:
        return [_enrich_offer_row(dict(r)) for r in conn.execute(sql, params).fetchall()]


def compare_search(q: str, *, limit: int = 40) -> list[dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return []
    # Case-insensitive match (ASCII + fold via lower on both sides)
    like = f"%{q.lower()}%"
    with db() as conn:
        rows = conn.execute(
            """
            SELECT o.*, s.name AS source_name, p.category AS category
            FROM offers o
            JOIN sources s ON s.id = o.source_id
            JOIN products p ON p.id = o.product_id
            WHERE s.enabled = 1 AND lower(o.title) LIKE ?
            ORDER BY o.price_dkk ASC
            LIMIT ?
            """,
            (like, limit),
        ).fetchall()
    return [_enrich_offer_row(dict(r)) for r in rows]


def recategorize_products() -> int:
    """Re-run title→category guessing after classifier fixes."""
    from app.collectors.tjek import guess_category

    updated = 0
    with db() as conn:
        rows = conn.execute("SELECT id, title, category FROM products").fetchall()
        for row in rows:
            new_cat = guess_category(row["title"])
            if new_cat != row["category"]:
                conn.execute(
                    "UPDATE products SET category = ? WHERE id = ?",
                    (new_cat, row["id"]),
                )
                updated += 1
    return updated


def list_items(user_key: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM list_items
            WHERE user_key = ?
            ORDER BY checked ASC, id DESC
            """,
            (user_key,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_list_item(user_key: str, label: str, product_id: int | None = None) -> None:
    label = label.strip()
    if not label:
        return
    with db() as conn:
        conn.execute(
            """
            INSERT INTO list_items (user_key, label, product_id, checked, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (user_key, label, product_id, _utc_now()),
        )


def toggle_list_item(user_key: str, item_id: int, checked: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE list_items SET checked = ? WHERE id = ? AND user_key = ?",
            (1 if checked else 0, item_id, user_key),
        )


def delete_list_item(user_key: str, item_id: int) -> None:
    with db() as conn:
        conn.execute(
            "DELETE FROM list_items WHERE id = ? AND user_key = ?",
            (item_id, user_key),
        )


def best_prices_for_list(user_key: str) -> list[dict[str, Any]]:
    items = [i for i in list_items(user_key) if not i.get("checked")]
    summary: list[dict[str, Any]] = []
    for item in items:
        matches = compare_search(item["label"], limit=5)
        if not matches:
            summary.append({"label": item["label"], "best": None, "by_store": []})
            continue
        by_store: dict[str, dict[str, Any]] = {}
        for m in matches:
            sid = m["source_id"]
            if sid not in by_store or float(m["price_dkk"]) < float(by_store[sid]["price_dkk"]):
                by_store[sid] = m
        best = min(by_store.values(), key=lambda r: float(r["price_dkk"]))
        summary.append({"label": item["label"], "best": best, "by_store": list(by_store.values())})
    return summary


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


def push_search(user_key: str, query: str) -> None:
    query = query.strip()
    if not query:
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO search_history (user_key, query, created_at) VALUES (?, ?, ?)",
            (user_key, query, _utc_now()),
        )
        # Keep last 20
        conn.execute(
            """
            DELETE FROM search_history
            WHERE user_key = ? AND id NOT IN (
              SELECT id FROM search_history WHERE user_key = ? ORDER BY id DESC LIMIT 20
            )
            """,
            (user_key, user_key),
        )


def recent_searches(user_key: str, limit: int = 8) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT query FROM search_history
            WHERE user_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_key, limit),
        ).fetchall()
    return [str(r["query"]) for r in rows]


def clear_search_history(user_key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM search_history WHERE user_key = ?", (user_key,))
