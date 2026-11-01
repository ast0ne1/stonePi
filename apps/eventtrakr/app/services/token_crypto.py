"""Encrypt Google OAuth tokens at rest in calendar_connections.

A Fernet key lives in the Vault (``EVENTTRAKR_TOKEN_KEY``). Plaintext values
written by older builds are still accepted on read and re-sealed on the next
write. SQLite dumps / backups then no longer expose live refresh tokens.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("eventtrakr.token_crypto")

_PREFIX = "etk1:"
_KEY_NAME = "EVENTTRAKR_TOKEN_KEY"
_fernet: Fernet | None = None


def _derived_key(secret: str) -> bytes:
    digest = hashlib.sha256(f"eventtrakr-tokens:{secret}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _local_key_path() -> Path:
    configured = (os.environ.get("STONEPI_DATA_DIR") or os.environ.get("DATA_DIR") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    return root / "eventtrakr-token.key"


def _local_durable_key() -> bytes:
    """Random Fernet key on disk when Vault and the session secret are both unavailable.

    Prefer Vault; never use a shared well-known string such as ``eventtrakr-dev``.
    """
    path = _local_key_path()
    if path.is_file():
        raw = path.read_bytes().strip()
        if raw:
            return raw
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key + b"\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def _load_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    key: bytes | None = None
    try:
        from stonepi_vault import get_secret, set_secret

        stored = (get_secret(_KEY_NAME, default="") or "").strip()
        if stored:
            key = stored.encode("utf-8")
        else:
            key = Fernet.generate_key()
            set_secret(_KEY_NAME, key.decode("ascii"))
    except Exception:
        logger.debug("Vault unavailable for token key; deriving from session secret", exc_info=True)
    if key is None:
        try:
            from app.services.auth import _platform_session_secret

            secret = (_platform_session_secret() or "").strip()
        except Exception:
            secret = ""
        if secret:
            key = _derived_key(secret)
        else:
            key = _local_durable_key()
    _fernet = Fernet(key)
    return _fernet


def reset_for_tests() -> None:
    global _fernet
    _fernet = None


def is_sealed(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_PREFIX)


def seal(value: str | None) -> str:
    """Encrypt a token for storage. Empty stays empty."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if is_sealed(raw):
        return raw
    token = _load_fernet().encrypt(raw.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def open_token(value: str | None) -> str:
    """Decrypt a stored token; plaintext legacy values pass through."""
    raw = value or ""
    if not raw:
        return ""
    if not is_sealed(raw):
        return raw
    blob = raw[len(_PREFIX) :]
    try:
        return _load_fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("Failed to decrypt calendar token; treating as empty")
        return ""
