from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.config import DATA_DIR, DB_PATH, ROOT_DIR

# Favorites belong to one person (``owner`` = their StonePi user id; "" when Radio runs
# without sign-in, as in solo dev). Prefs are household-wide: there is one speaker.
# Stations are the household's list on My Radio: filled once from default_stations.json,
# then anyone with Radio adds to it; admins remove any, others the ones they added
# (``added_by`` "" = built in).

DEFAULT_STATIONS = ROOT_DIR / "app" / "fixtures" / "default_stations.json"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS favorites (
              owner TEXT NOT NULL DEFAULT '',
              id TEXT NOT NULL,
              name TEXT NOT NULL,
              stream_url TEXT NOT NULL,
              favicon TEXT,
              tags TEXT,
              country TEXT,
              created_at TEXT NOT NULL,
              PRIMARY KEY (owner, id)
            );
            CREATE TABLE IF NOT EXISTS prefs (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS favorites_owner_url ON favorites(owner, stream_url);
            CREATE TABLE IF NOT EXISTS stations (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              stream_url TEXT NOT NULL UNIQUE,
              favicon TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              country TEXT NOT NULL DEFAULT '',
              homepage TEXT NOT NULL DEFAULT '',
              added_by TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS person_prefs (
              owner TEXT NOT NULL,
              key TEXT NOT NULL,
              value TEXT NOT NULL,
              PRIMARY KEY (owner, key)
            );
            CREATE TABLE IF NOT EXISTS speakers (
              udn TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '',
              host TEXT NOT NULL,
              location TEXT NOT NULL,
              av_control TEXT NOT NULL,
              rc_control TEXT NOT NULL DEFAULT '',
              linkplay INTEGER NOT NULL DEFAULT 0,
              use_linkplay INTEGER NOT NULL DEFAULT 0,
              direct_https INTEGER NOT NULL DEFAULT 0,
              last_seen REAL NOT NULL,
              now_title TEXT NOT NULL DEFAULT '',
              now_station TEXT NOT NULL DEFAULT ''
            );
            """
        )
    _seed_stations_once()


def _default_stations() -> list[dict]:
    try:
        raw = json.loads(DEFAULT_STATIONS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("stream_url")]


def _seed_stations_once() -> None:
    """The built-in list goes in on first start only, so removed stations stay removed."""
    if get_pref("stations_seeded") == "1":
        return
    for item in _default_stations():
        try:
            add_station(
                "",
                name=str(item.get("name") or ""),
                stream_url=str(item.get("stream_url") or ""),
                favicon=str(item.get("favicon") or ""),
                tags=str(item.get("tags") or ""),
                country=str(item.get("country") or ""),
                homepage=str(item.get("homepage") or ""),
                station_id=str(item.get("id") or "") or None,
            )
        except ValueError:
            continue
    set_pref("stations_seeded", "1")


def _row_to_household(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "stream_url": row["stream_url"],
        "favicon": row["favicon"],
        "tags": row["tags"],
        "country": row["country"],
        "homepage": row["homepage"],
        "added_by": row["added_by"],
        "in_list": True,
        "source": "household",
    }


def list_stations() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM stations ORDER BY rowid").fetchall()
    return [_row_to_household(r) for r in rows]


def get_station(station_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    return _row_to_household(row) if row else None


def find_station_by_url(stream_url: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stations WHERE stream_url = ?", (stream_url.strip(),)).fetchone()
    return _row_to_household(row) if row else None


def station_urls() -> set[str]:
    with _connect() as conn:
        return {r[0] for r in conn.execute("SELECT stream_url FROM stations")}


def add_station(
    added_by: str,
    *,
    name: str,
    stream_url: str,
    favicon: str = "",
    tags: str = "",
    country: str = "",
    homepage: str = "",
    station_id: str | None = None,
) -> dict:
    stream_url = (stream_url or "").strip()
    if not stream_url:
        raise ValueError("stream_url required")
    existing = find_station_by_url(stream_url)
    if existing:
        return existing
    station_id = (station_id or "").strip()[:40] or uuid.uuid4().hex[:12]
    if get_station(station_id):
        station_id = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO stations (id, name, stream_url, favicon, tags, country, homepage, added_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                station_id,
                (name or "").strip()[:120] or "Station",
                stream_url,
                (favicon or "").strip()[:500],
                (tags or "").strip()[:200],
                (country or "").strip()[:80],
                (homepage or "").strip()[:500],
                added_by,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return get_station(station_id) or {}


def remove_station(station_id: str) -> bool:
    with _connect() as conn:
        return conn.execute("DELETE FROM stations WHERE id = ?", (station_id,)).rowcount > 0


def _row_to_station(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "stream_url": row["stream_url"],
        "favicon": row["favicon"] or "",
        "tags": row["tags"] or "",
        "country": row["country"] or "",
        "created_at": row["created_at"],
        "favorite": True,
    }


def list_favorites(owner: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM favorites WHERE owner = ? ORDER BY created_at DESC", (owner,)
        ).fetchall()
    return [_row_to_station(r) for r in rows]


def household_favorites(limit: int = 4) -> list[dict]:
    """Stations for displays nobody is signed in to (Car Thing, Home): most-saved first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT f.* FROM favorites f
            JOIN (
              SELECT stream_url, COUNT(*) AS saves, MAX(created_at) AS latest
              FROM favorites GROUP BY stream_url
            ) g ON g.stream_url = f.stream_url
            WHERE f.created_at = g.latest
            GROUP BY f.stream_url
            ORDER BY g.saves DESC, g.latest DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_station(r) for r in rows]


def get_favorite(owner: str, station_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM favorites WHERE owner = ? AND id = ?", (owner, station_id)
        ).fetchone()
    return _row_to_station(row) if row else None


def find_favorite_by_url(owner: str, stream_url: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM favorites WHERE owner = ? AND stream_url = ?", (owner, stream_url.strip())
        ).fetchone()
    return _row_to_station(row) if row else None


def find_any_by_url(stream_url: str) -> dict | None:
    """Someone's saved copy of a stream, for name and artwork when a display plays it."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM favorites WHERE stream_url = ? ORDER BY created_at DESC LIMIT 1",
            (stream_url.strip(),),
        ).fetchone()
    return _row_to_station(row) if row else None


def add_favorite(
    owner: str,
    *,
    name: str,
    stream_url: str,
    favicon: str = "",
    tags: str = "",
    country: str = "",
    station_id: str | None = None,
) -> dict:
    name = (name or "").strip() or "Station"
    stream_url = (stream_url or "").strip()
    if not stream_url:
        raise ValueError("stream_url required")
    existing = find_favorite_by_url(owner, stream_url)
    if existing:
        return existing
    item = {
        "id": (station_id or uuid.uuid4().hex[:12]).strip(),
        "name": name,
        "stream_url": stream_url,
        "favicon": (favicon or "").strip(),
        "tags": (tags or "").strip(),
        "country": (country or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # The same station saved again under a new address (a station moved its stream): update
    # that favorite rather than clash on its id.
    with _connect() as conn:
        conn.execute(
            "INSERT INTO favorites (owner, id, name, stream_url, favicon, tags, country, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(owner, id) DO UPDATE SET name = excluded.name, stream_url = excluded.stream_url,"
            " favicon = excluded.favicon, tags = excluded.tags, country = excluded.country",
            (
                owner,
                item["id"],
                item["name"],
                item["stream_url"],
                item["favicon"],
                item["tags"],
                item["country"],
                item["created_at"],
            ),
        )
    item["favorite"] = True
    return item


def remove_favorite(owner: str, station_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM favorites WHERE owner = ? AND id = ?", (owner, station_id))
        return cur.rowcount > 0


def favorite_urls(owner: str) -> set[str]:
    return {f["stream_url"] for f in list_favorites(owner)}


def get_pref(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_pref(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prefs (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def seed_mock_favorites_if_empty(owner: str = "") -> None:
    if list_favorites(owner):
        return
    for station in _default_stations()[:4]:
        try:
            add_favorite(
                owner,
                name=str(station.get("name") or "Station"),
                stream_url=str(station.get("stream_url") or ""),
                favicon=str(station.get("favicon") or ""),
                tags=str(station.get("tags") or ""),
                country=str(station.get("country") or ""),
                station_id=str(station.get("id") or "") or None,
            )
        except ValueError:
            continue


# -- Per-person prefs (where Radio plays for you) -------------------------------------------------


def get_person_pref(owner: str, key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM person_prefs WHERE owner = ? AND key = ?", (owner, key)).fetchone()
    return str(row["value"]) if row else default


def set_person_pref(owner: str, key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO person_prefs (owner, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value",
            (owner, key, value),
        )


# -- Speakers (DLNA renderers found on the home network) ------------------------------------------

SPEAKER_KEEP_S = 3600  # listed for an hour after it was last seen


def _row_to_speaker(row: sqlite3.Row) -> dict:
    out = {k: row[k] for k in row.keys()}
    for flag in ("linkplay", "use_linkplay", "direct_https"):
        out[flag] = bool(out[flag])
    out["shown_name"] = out["label"] or out["name"]
    return out


def upsert_speaker(found: dict, now: float) -> None:
    """Record a renderer from discovery; keeps the household's label and switches."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO speakers (udn, name, model, host, location, av_control, rc_control, linkplay, last_seen)
            VALUES (:udn, :name, :model, :host, :location, :av_control, :rc_control, :linkplay, :now)
            ON CONFLICT(udn) DO UPDATE SET name = excluded.name, model = excluded.model, host = excluded.host,
              location = excluded.location, av_control = excluded.av_control, rc_control = excluded.rc_control,
              linkplay = excluded.linkplay OR speakers.linkplay, last_seen = excluded.last_seen
            """,
            {**found, "linkplay": int(bool(found.get("linkplay"))), "now": now},
        )


def list_speakers(now: float, *, keep_s: float = SPEAKER_KEEP_S) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM speakers WHERE last_seen >= ? ORDER BY COALESCE(NULLIF(label, ''), name)", (now - keep_s,)
        ).fetchall()
    return [_row_to_speaker(r) for r in rows]


def get_speaker(udn: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM speakers WHERE udn = ?", (udn,)).fetchone()
    return _row_to_speaker(row) if row else None


def update_speaker(udn: str, **fields) -> None:
    allowed = {"label", "use_linkplay", "direct_https", "now_title", "now_station", "linkplay"}
    sets = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with _connect() as conn:
        conn.execute(
            f"UPDATE speakers SET {', '.join(f'{k} = :{k}' for k in sets)} WHERE udn = :udn",  # keys from allowed only
            {**sets, "udn": udn},
        )
