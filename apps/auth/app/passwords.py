from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def is_hashed(value: str | None) -> bool:
    return (value or "").strip().startswith("$argon2")


def hash_password(password: str) -> str:
    return _hasher.hash(password or "")


def verify_password(stored: str, password: str) -> bool:
    raw = (stored or "").strip()
    if not raw:
        return False
    if is_hashed(raw):
        try:
            return _hasher.verify(raw, password or "")
        except (VerifyMismatchError, InvalidHashError):
            return False
    import secrets

    return secrets.compare_digest(raw.encode("utf-8"), (password or "").encode("utf-8")) if len(raw) == len(password or "") else False


def needs_rehash(stored: str) -> bool:
    raw = (stored or "").strip()
    if not is_hashed(raw):
        return True
    try:
        return _hasher.check_needs_rehash(raw)
    except Exception:
        return True
