"""Audit log for StonePi services (ideation/platform-plumbing.md, P3).

Each service keeps its own SQLite file; there is no central audit service::

    audit = AuditLog(data_dir / "audit.db", retention_days=90)
    audit.record(actor="binding:7", subject="adam", action="get_status",
                 decision=ALLOWED, params={"days": 3}, size=412, duration_ms=38)
    audit.query(since=..., actor=..., decision=...)

``params`` pass through :func:`summarise`: scalars are capped at 80 characters, nested
values are reduced to a type note, and keys that look secret are dropped. Never pass
prompts, transcripts or outputs: the log records that something happened, not what was said.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ALLOWED = "ALLOWED"
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
FAILED = "FAILED"


def denied(reason: str) -> str:
    return f"DENIED:{reason}"


VALUE_CAP = 80
_SECRETISH = re.compile(r"(pass|secret|token|key|pin|auth|cookie|session)", re.IGNORECASE)
_PRUNE_EVERY_S = 3600


def summarise(params: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (params or {}).items():
        key = str(key)[:40]
        if _SECRETISH.search(key):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value if len(value) <= VALUE_CAP else value[: VALUE_CAP - 1] + "…"
        elif isinstance(value, (list, tuple, dict)):
            out[key] = f"<{type(value).__name__}:{len(value)}>"
        else:
            out[key] = f"<{type(value).__name__}>"
    return out


class AuditLog:
    def __init__(self, path: Path | str, *, retention_days: int = 90):
        self.path = Path(path)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._last_prune = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    actor TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    params TEXT NOT NULL DEFAULT '{}',
                    size INTEGER,
                    duration_ms INTEGER,
                    note TEXT NOT NULL DEFAULT ''
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts)")
            db.execute("CREATE INDEX IF NOT EXISTS audit_actor ON audit (actor, ts)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:  # commits, or rolls back on error
                yield db
        finally:
            db.close()

    def record(
        self,
        *,
        actor: str,
        action: str,
        decision: str,
        subject: str = "",
        params: dict[str, Any] | None = None,
        size: int | None = None,
        duration_ms: int | None = None,
        note: str = "",
        now: float | None = None,
    ) -> None:
        ts = now if now is not None else time.time()
        row = (ts, actor[:80], subject[:80], action[:80], decision[:80],
               json.dumps(summarise(params), ensure_ascii=False), size, duration_ms, note[:VALUE_CAP])
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO audit (ts, actor, subject, action, decision, params, size, duration_ms, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            if ts - self._last_prune > _PRUNE_EVERY_S:
                self._last_prune = ts
                db.execute("DELETE FROM audit WHERE ts < ?", (ts - self.retention_days * 86400,))

    def query(
        self,
        *,
        since: float | None = None,
        actor: str | None = None,
        decision: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if since is not None:
            clauses.append("ts >= ?")
            args.append(since)
        if actor:
            clauses.append("actor = ?")
            args.append(actor)
        if decision:
            # "DENIED" matches every DENIED:<reason>.
            clauses.append("(decision = ? OR decision LIKE ?)")
            args.extend([decision, f"{decision}:%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM audit {where} ORDER BY ts DESC, id DESC LIMIT ?", (*args, max(1, min(limit, 1000)))
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["params"] = json.loads(item["params"])
            except ValueError:
                item["params"] = {}
            out.append(item)
        return out


__all__ = ["ALLOWED", "APPROVED", "AuditLog", "EXPIRED", "FAILED", "PENDING", "REJECTED", "denied", "summarise"]
