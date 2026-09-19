#!/usr/bin/env python3
"""Move plaintext app secrets from SQLite settings into StonePi Vault."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NEWSCAST_SETTING_TO_VAULT: dict[str, str] = {
    "openai_api_key": "OPENAI_API_KEY",
    "x3_sync_token": "X3_SYNC_TOKEN",
    "reader_ssh_password": "NEWSCAST_READER_SSH_PASSWORD",
    "ntfy_token": "NEWSCAST_NTFY_TOKEN",
}

EVENTTRAKR_SETTING_TO_VAULT: dict[str, str] = {
    "brightdata_api_key": "BRIGHTDATA_API_KEY",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
}

USER_SETTING_TO_VAULT: dict[str, str] = {
    "x3_sync_token": "NEWSCAST_USER_{user_id}_X3_SYNC_TOKEN",
    "ntfy_token": "NEWSCAST_USER_{user_id}_NTFY_TOKEN",
}


def open_db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        print(f"skip missing {path}")
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def migrate_settings_table(
    conn: sqlite3.Connection,
    table: str,
    mapping: dict[str, str],
    *,
    label: str,
) -> int:
    if not table_exists(conn, table):
        return 0
    from stonepi_vault import set_secret

    moved = 0
    for setting_key, vault_key in mapping.items():
        row = conn.execute(
            f"SELECT value FROM {table} WHERE key = ?",
            (setting_key,),
        ).fetchone()
        if row is None:
            continue
        value = (row["value"] or "").strip()
        if not value:
            continue
        set_secret(vault_key, value)
        conn.execute(f"DELETE FROM {table} WHERE key = ?", (setting_key,))
        moved += 1
        print(f"{label}: {setting_key} -> {vault_key}")
    conn.commit()
    return moved


def migrate_user_settings(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "user_settings"):
        return 0
    from stonepi_vault import set_secret

    moved = 0
    rows = conn.execute(
        "SELECT user_id, key, value FROM user_settings WHERE key IN (?, ?)",
        tuple(USER_SETTING_TO_VAULT.keys()),
    ).fetchall()
    for row in rows:
        value = (row["value"] or "").strip()
        if not value:
            continue
        template = USER_SETTING_TO_VAULT[row["key"]]
        vault_key = template.format(user_id=int(row["user_id"]))
        set_secret(vault_key, value)
        conn.execute(
            "DELETE FROM user_settings WHERE user_id = ? AND key = ?",
            (int(row["user_id"]), row["key"]),
        )
        moved += 1
        print(f"newscast user {row['user_id']}: {row['key']} -> {vault_key}")
    conn.commit()
    return moved


def migrate_newscast(path: Path) -> int:
    conn = open_db(path)
    if conn is None:
        return 0
    try:
        total = migrate_settings_table(conn, "settings", NEWSCAST_SETTING_TO_VAULT, label="newscast")
        total += migrate_user_settings(conn)
        return total
    finally:
        conn.close()


def migrate_eventtrakr(path: Path) -> int:
    conn = open_db(path)
    if conn is None:
        return 0
    try:
        return migrate_settings_table(
            conn, "settings", EVENTTRAKR_SETTING_TO_VAULT, label="eventtrakr"
        )
    finally:
        conn.close()


def default_vault_dir() -> Path:
    env = os.environ.get("STONEPI_VAULT_DIR", "").strip()
    if env:
        return Path(env)
    return ROOT / "data" / "vault"


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate app DB secrets into StonePi Vault")
    parser.add_argument(
        "--newscast-db",
        type=Path,
        default=ROOT / "apps" / "newscast" / "data" / "newscast.db",
    )
    parser.add_argument(
        "--eventtrakr-db",
        type=Path,
        default=ROOT / "apps" / "eventtrakr" / "data" / "eventtrakr.db",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Defaults to STONEPI_VAULT_DIR or repo data/vault",
    )
    args = parser.parse_args()

    vault_dir = args.vault_dir or default_vault_dir()
    os.environ.setdefault("STONEPI_VAULT_DIR", str(vault_dir))

    try:
        from stonepi_vault import configure  # noqa: F401
    except ImportError:
        print(
            "stonepi_vault not installed; run with an app venv "
            "(e.g. apps/dashboard/.venv/bin/python)",
            file=sys.stderr,
        )
        return 1

    from stonepi_vault import configure

    configure(vault_dir)
    moved = migrate_newscast(args.newscast_db)
    moved += migrate_eventtrakr(args.eventtrakr_db)
    print(f"done: migrated={moved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
