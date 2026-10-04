from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app import teams
from app.config import DATA_DIR, DB_PATH, SPORT_LEAGUES


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
            ("wheresthematch", "timezone.football + WheresTheMatch"),
        ):
            conn.execute(
                """
                INSERT INTO sources (id, name, enabled) VALUES (?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name
                """,
                (sid, name),
            )
        # Rugby used to store a flat "Rugby" league; split it until the next refresh replaces rows.
        from app.timeutil import normalize_rugby_league

        for rid, title in conn.execute(
            "SELECT id, title FROM listings WHERE sport = 'rugby' AND league IN ('', 'Rugby')"
        ).fetchall():
            conn.execute("UPDATE listings SET league = ? WHERE id = ?", (normalize_rugby_league(title), rid))


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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


def delete_pref(user_key: str, key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM prefs WHERE user_key = ? AND key = ?", (user_key, key))


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
        named = [x for x in SPORT_LEAGUES.get(sport or "", ()) if x != "Other"]
        if league == "Other" and named:
            clauses.append(f"league NOT IN ({','.join('?' * len(named))})")
            params.extend(named)
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


# Standalone / pre-platform prefs live under this key; under StonePi sign-in
# each person's prefs use their Auth user id.
LOCAL_KEY = "local"
DEFAULT_TIMEZONE = "Australia/Melbourne"
LOCAL_MOVED_META = "local_prefs_moved_to"
WATCHED_TEAMS_KEY = "watched_teams"
APPROACHING_LEAD_KEY = "notify_approaching_minutes"
APPROACHING_SENT_KEY = "notify_approaching_sent"
DEFAULT_APPROACHING_MINUTES = 30


def watched_id(entry: dict[str, Any]) -> str:
    """Stable id for one watched team: "sport:key", or just "key" for legacy entries."""
    sport = str(entry.get("sport") or "")
    return f"{sport}:{entry['key']}" if sport else str(entry["key"])


def _clean_entries(items: list[Any]) -> list[dict[str, str]]:
    """[{key, name[, sport]}] from names or dicts, keyed by teams.team_key and de-duplicated.

    ``sport`` is set for teams picked from the list (the same name can be watched
    in two sports, e.g. Australia in cricket and rugby); legacy names have none.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        sport = ""
        if isinstance(item, dict):
            name = teams.clean_name(str(item.get("name") or item.get("key") or ""))
            key = teams.team_key(str(item.get("key") or "") or name)
            sport = str(item.get("sport") or "").strip()
        else:
            name = teams.clean_name(str(item or ""))
            key = teams.team_key(name)
        entry = {"key": key, "name": name or key}
        if sport:
            entry["sport"] = sport
        if len(key) < 2 or watched_id(entry) in seen:
            continue
        seen.add(watched_id(entry))
        out.append(entry)
    return out


def _parse_watched(raw: str) -> tuple[list[dict[str, str]], bool]:
    """Stored Teams to Watch as [{key, name}], and whether it was a legacy value.

    Legacy values were free text: a JSON list of names (an entry may itself hold
    commas) or a bare comma/newline-separated string. They split into one entry
    per name.
    """
    raw = (raw or "").strip()
    try:
        data = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        data = raw
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list):
        return [], True
    items: list[Any] = []
    legacy = False
    for item in data:
        if isinstance(item, dict):
            items.append(item)
            continue
        legacy = True
        items.extend(re.split(r"[,;\n]", str(item or "")))
    return _clean_entries(items), legacy


def get_watched_entries(user_key: str = "local") -> list[dict[str, str]]:
    """Teams to Watch as [{key, name}]. Legacy free-text values are migrated on read."""
    raw = get_pref(user_key, WATCHED_TEAMS_KEY, "")
    if not raw:
        return []
    entries, legacy = _parse_watched(raw)
    if legacy:
        set_pref(user_key, WATCHED_TEAMS_KEY, json.dumps(entries, ensure_ascii=False))
    return entries


def get_watched_teams(user_key: str = "local") -> list[str]:
    """Display names of the person's teams."""
    return [e["name"] for e in get_watched_entries(user_key)]


def set_watched_teams(user_key: str, entries: list[Any]) -> list[str]:
    """Replace the list; items are names or {key, name} dicts. Returns display names."""
    cleaned = _clean_entries(list(entries))
    set_pref(user_key, WATCHED_TEAMS_KEY, json.dumps(cleaned, ensure_ascii=False))
    return [e["name"] for e in cleaned]


def add_watched_team(user_key: str, key: str, name: str, sport: str = "") -> bool:
    """Add one team (by key, in a sport). False when it's already watched or empty."""
    entries = get_watched_entries(user_key)
    key = teams.team_key(key or name)
    entry = {"key": key, "name": name or key, **({"sport": sport} if sport else {})}
    if len(key) < 2 or any(watched_id(e) == watched_id(entry) for e in entries):
        return False
    set_watched_teams(user_key, entries + [entry])
    return True


def remove_watched_team(user_key: str, ident: str) -> bool:
    """Remove by ``watched_id`` ("sport:key"); a bare key also works (legacy forms)."""
    entries = get_watched_entries(user_key)
    ident = str(ident or "").strip()
    if ":" in ident:
        sport, _, key = ident.partition(":")
        ident = f"{sport}:{teams.team_key(key)}"
    else:
        ident = teams.team_key(ident)
    kept = [e for e in entries if watched_id(e) != ident and (":" in ident or e["key"] != ident)]
    if len(kept) == len(entries):
        return False
    set_watched_teams(user_key, kept)
    return True


def listing_titles() -> list[dict[str, Any]]:
    """Sport and title of every collected listing (for the Teams to Watch picker)."""
    with db() as conn:
        rows = conn.execute("SELECT DISTINCT sport, title FROM listings").fetchall()
    return [dict(r) for r in rows]


def approaching_lead_minutes(user_key: str = "local") -> int:
    raw = get_pref(user_key, APPROACHING_LEAD_KEY, str(DEFAULT_APPROACHING_MINUTES))
    try:
        value = int(str(raw or DEFAULT_APPROACHING_MINUTES).strip())
    except (TypeError, ValueError):
        value = DEFAULT_APPROACHING_MINUTES
    return max(5, min(24 * 60, value))


def set_approaching_lead_minutes(user_key: str, minutes: int) -> int:
    value = max(5, min(24 * 60, int(minutes)))
    set_pref(user_key, APPROACHING_LEAD_KEY, str(value))
    return value


def get_approaching_sent(user_key: str = "local") -> dict[str, str]:
    raw = get_pref(user_key, APPROACHING_SENT_KEY, "{}")
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def set_approaching_sent(user_key: str, sent: dict[str, str]) -> None:
    set_pref(user_key, APPROACHING_SENT_KEY, json.dumps(sent))


def household_timezone() -> str:
    """Household timezone (display widget, and the default for new people)."""
    return get_pref(LOCAL_KEY, "timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE


def user_timezone(user_key: str) -> str:
    return get_pref(user_key, "timezone", household_timezone()) or household_timezone()


def watching_user_keys() -> list[str]:
    """Everyone with at least one team to watch."""
    with db() as conn:
        rows = conn.execute("SELECT user_key FROM prefs WHERE key = ?", (WATCHED_TEAMS_KEY,)).fetchall()
    return sorted(str(r["user_key"]) for r in rows if get_watched_teams(str(r["user_key"])))


def all_watched_entries() -> list[dict[str, str]]:
    """Union of everyone's watched teams (entries), for the household display widget."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for user_key in watching_user_keys():
        for entry in get_watched_entries(user_key):
            if watched_id(entry) not in seen:
                seen.add(watched_id(entry))
                out.append(entry)
    return out


def all_watched_teams() -> list[str]:
    """Display names of everyone's teams (household display widget)."""
    out: list[str] = []
    for entry in all_watched_entries():
        if entry["name"] not in out:
            out.append(entry["name"])
    return out


def move_local_prefs_to(user_key: str) -> bool:
    """One-time move of the shared (pre-platform) prefs to the first admin.

    Teams to Watch, lead time and sent-alert state move; city and timezone are
    copied (the household timezone stays for the display and as the default).
    The person's own values win. Returns True when this call did the move.
    """
    if not user_key or user_key == LOCAL_KEY or get_meta(LOCAL_MOVED_META):
        return False
    for key in (WATCHED_TEAMS_KEY, APPROACHING_LEAD_KEY, APPROACHING_SENT_KEY, "city", "timezone"):
        shared = get_pref(LOCAL_KEY, key, "")
        if shared and not get_pref(user_key, key, ""):
            set_pref(user_key, key, shared)
    for key in (WATCHED_TEAMS_KEY, APPROACHING_LEAD_KEY, APPROACHING_SENT_KEY):
        delete_pref(LOCAL_KEY, key)
    set_meta(LOCAL_MOVED_META, user_key)
    return True
