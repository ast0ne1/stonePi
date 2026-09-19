#!/usr/bin/env python3
"""Import household users from NewsCast / FileServe / EventTrakr into StonePi auth.

Matching is by username. The first argon2 hash wins; later clashes are reported
and the extra app grant is still added.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCES = {
    "newscast": ("password", "newscast"),
    "fileserve": ("password_hash", "fileserve"),
    "eventtrakr": ("password", "eventtrakr"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def open_db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        print(f"skip missing {path}")
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def load_app_users(path: Path, hash_column: str) -> list[dict]:
    conn = open_db(path)
    if conn is None:
        return []
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "username" not in cols:
            return []
        password_col = hash_column if hash_column in cols else "password"
        rows = conn.execute(
            f"SELECT username, {password_col} AS password_hash, role, active FROM users"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"could not read {path}: {exc}")
        return []
    finally:
        conn.close()
    users = []
    for row in rows:
        name = (row["username"] or "").strip().lower()
        if not name:
            continue
        users.append(
            {
                "username": name,
                "password_hash": row["password_hash"] or "",
                "is_admin": str(row["role"] or "") == "admin",
                "enabled": bool(row["active"]) if row["active"] is not None else True,
            }
        )
    return users


def ensure_grant(conn: sqlite3.Connection, user_id: str, app_id: str) -> None:
    existing = conn.execute(
        "SELECT 1 FROM app_grants WHERE user_id = ? AND app_id = ?",
        (user_id, app_id),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO app_grants (id, user_id, app_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, app_id),
    )


def migrate(auth_db: Path, sources: dict[str, Path]) -> int:
    conn = open_db(auth_db)
    if conn is None:
        print(f"auth database not found: {auth_db}")
        return 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        print("auth database has no users table yet; start stonepi-auth once, then re-run")
        conn.close()
        return 1
    clashes = 0
    imported = 0
    now = utcnow()
    try:
        for app_id, path in sources.items():
            hash_column, grant = APP_SOURCES[app_id]
            for user in load_app_users(path, hash_column):
                row = conn.execute(
                    "SELECT id, password_hash FROM users WHERE username = ?",
                    (user["username"],),
                ).fetchone()
                if row is None:
                    user_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO users (
                            id, username, display_name, password_hash, enabled, is_admin, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            user["username"],
                            user["username"],
                            user["password_hash"],
                            int(user["enabled"]),
                            int(user["is_admin"]),
                            now,
                            now,
                        ),
                    )
                    imported += 1
                    print(f"imported {user['username']} from {app_id}")
                else:
                    user_id = row["id"]
                    incoming = user["password_hash"]
                    stored = row["password_hash"] or ""
                    if incoming and stored and incoming != stored and incoming.startswith("$argon2") and stored.startswith("$argon2"):
                        print(f"password clash for {user['username']} from {app_id}; keeping existing hash")
                        clashes += 1
                    elif incoming.startswith("$argon2") and not stored.startswith("$argon2"):
                        conn.execute(
                            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                            (incoming, now, user_id),
                        )
                ensure_grant(conn, user_id, grant)
                ensure_grant(conn, user_id, "dashboard")
        conn.commit()
    finally:
        conn.close()
    print(f"done: imported={imported} clashes={clashes}")
    return 0


def default_sources(root: Path) -> dict[str, Path]:
    return {
        "newscast": root / "apps" / "newscast" / "data" / "newscast.db",
        "fileserve": root / "apps" / "fileserve" / "data" / "fileserve.db",
        "eventtrakr": root / "apps" / "eventtrakr" / "data" / "eventtrakr.db",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate app users into StonePi auth")
    parser.add_argument("--auth-db", type=Path, default=ROOT / "apps" / "auth" / "data" / "users.sqlite")
    parser.add_argument("--newscast-db", type=Path)
    parser.add_argument("--fileserve-db", type=Path)
    parser.add_argument("--eventtrakr-db", type=Path)
    args = parser.parse_args()
    sources = default_sources(ROOT)
    if args.newscast_db:
        sources["newscast"] = args.newscast_db
    if args.fileserve_db:
        sources["fileserve"] = args.fileserve_db
    if args.eventtrakr_db:
        sources["eventtrakr"] = args.eventtrakr_db
    return migrate(args.auth_db, sources)


if __name__ == "__main__":
    sys.exit(main())
