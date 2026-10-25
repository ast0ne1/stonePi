"""Bindings: one agent identity ↔ exactly one StonePi user (muse.md §3, plumbing P5).

For Pebble the agent identity is the device (``agent_ref`` = its ``pbl-…`` id) and the user
is whoever claimed it. A device has at most one live binding; claiming it again revokes the
old one. Tokens are stored as SHA-256 only.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BOUND = "BOUND"
REVOKED = "REVOKED"

DEFAULT_TALK = {"hold": True, "toggle": False, "wake": False, "wake_phrase": "hey stonepi"}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_pebble_token() -> str:
    return "pbt_" + secrets.token_urlsafe(32)  # 43 chars → 47 total (PROTOCOL.md §4)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adapter TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    agent_ref TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    created REAL NOT NULL,
                    revoked REAL,
                    policy TEXT NOT NULL DEFAULT '{}',
                    settings TEXT NOT NULL DEFAULT '{}',
                    rev INTEGER NOT NULL DEFAULT 1,
                    last_seen REAL,
                    last_path TEXT NOT NULL DEFAULT '',
                    fw TEXT NOT NULL DEFAULT '',
                    battery TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    last_ask REAL,
                    last_ask_ok REAL
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS bindings_ref ON bindings (adapter, agent_ref, state)")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for key in ("policy", "settings"):
            try:
                item[key] = json.loads(item[key] or "{}")
            except ValueError:
                item[key] = {}
        return item

    # -- reads ---------------------------------------------------------------------------

    def get(self, binding_id: int) -> dict[str, Any] | None:
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM bindings WHERE id = ?", (binding_id,)).fetchone())

    def by_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM bindings WHERE token_hash = ? AND state = ?", (hash_token(token), BOUND)
            ).fetchone()
        return self._row(row)

    def list(self, *, adapter: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM bindings WHERE state = ?", [BOUND]
        if adapter:
            sql += " AND adapter = ?"
            args.append(adapter)
        with self._db() as db:
            rows = db.execute(sql + " ORDER BY created", args).fetchall()
        return [self._row(r) for r in rows]

    # -- writes --------------------------------------------------------------------------

    def create_pebble(
        self, *, device_id: str, user_id: str, user_name: str, nickname: str = "", fw: str = "", model: str = ""
    ) -> tuple[dict[str, Any], str]:
        """New binding for a claimed device; any older binding for it is revoked first."""
        token = new_pebble_token()
        now = time.time()
        with self._lock, self._db() as db:
            old = db.execute(
                "SELECT settings, rev FROM bindings WHERE adapter = 'pebble' AND agent_ref = ? ORDER BY id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            db.execute(
                "UPDATE bindings SET state = ?, revoked = ? WHERE adapter = 'pebble' AND agent_ref = ? AND state = ?",
                (REVOKED, now, device_id, BOUND),
            )
            settings = {"nickname": "Pebble", "talk": dict(DEFAULT_TALK), "away_allowed": False}
            if old is not None:
                try:
                    kept = json.loads(old["settings"] or "{}")
                except ValueError:
                    kept = {}
                # Keep the device's name and talk settings across re-claims; away use starts off again.
                settings.update({k: kept[k] for k in ("nickname", "talk") if k in kept})
            if nickname.strip():
                settings["nickname"] = nickname.strip()[:24]
            rev = (int(old["rev"]) + 1) if old is not None else 1
            cur = db.execute(
                """INSERT INTO bindings (adapter, label, agent_ref, user_id, user_name, token_hash, state, created,
                   settings, rev, last_seen, last_path, fw, model)
                   VALUES ('pebble', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lan', ?, ?)""",
                (settings["nickname"], device_id, user_id, user_name, hash_token(token), BOUND, now,
                 json.dumps(settings), rev, now, fw[:32], model[:48]),
            )
            binding_id = cur.lastrowid
        return self.get(binding_id), token

    def update_settings(self, binding_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            current = self.get(binding_id)
            if current is None or current["state"] != BOUND:
                return None
            settings = {**current["settings"], **patch}
            with self._db() as db:
                db.execute(
                    "UPDATE bindings SET settings = ?, label = ?, rev = rev + 1 WHERE id = ?",
                    (json.dumps(settings), str(settings.get("nickname") or current["label"]), binding_id),
                )
        return self.get(binding_id)

    def set_policy(self, binding_id: int, tool: str, enabled: bool) -> None:
        with self._lock:
            current = self.get(binding_id)
            if current is None:
                return
            policy = {**current["policy"], tool: "allow" if enabled else "deny"}
            with self._db() as db:
                db.execute("UPDATE bindings SET policy = ? WHERE id = ?", (json.dumps(policy), binding_id))

    def revoke(self, binding_id: int) -> bool:
        with self._lock, self._db() as db:
            cur = db.execute(
                "UPDATE bindings SET state = ?, revoked = ? WHERE id = ? AND state = ?",
                (REVOKED, time.time(), binding_id, BOUND),
            )
            return cur.rowcount > 0

    def touch(self, binding_id: int, *, path: str, fw: str = "", battery: str = "") -> None:
        with self._db() as db:
            db.execute(
                "UPDATE bindings SET last_seen = ?, last_path = ?, fw = CASE WHEN ? != '' THEN ? ELSE fw END,"
                " battery = ? WHERE id = ?",
                (time.time(), path, fw[:32], fw[:32], battery[:4], binding_id),
            )

    def mark_ask(self, binding_id: int, *, ok: bool) -> None:
        now = time.time()
        with self._db() as db:
            if ok:
                db.execute("UPDATE bindings SET last_ask = ?, last_ask_ok = ? WHERE id = ?", (now, now, binding_id))
            else:
                db.execute("UPDATE bindings SET last_ask = ? WHERE id = ?", (now, binding_id))
