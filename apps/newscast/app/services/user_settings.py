from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import UserSetting
from app.services import settings as instance_settings

USER_VAULT_SECRET_KEYS: dict[str, str] = {
    "x3_sync_token": "NEWSCAST_USER_{user_id}_X3_SYNC_TOKEN",
    "ntfy_token": "NEWSCAST_USER_{user_id}_NTFY_TOKEN",
}


def _user_vault_key(user_id: int, key: str) -> str | None:
    template = USER_VAULT_SECRET_KEYS.get(key)
    if not template:
        return None
    return template.format(user_id=int(user_id))


def _read_user_vault(user_id: int, key: str) -> str:
    vault_key = _user_vault_key(user_id, key)
    if not vault_key:
        return ""
    try:
        from stonepi_vault import get_secret

        return get_secret(vault_key, env_name=vault_key, default="")
    except Exception:
        return ""


def _write_user_vault(user_id: int, key: str, value: str) -> bool:
    vault_key = _user_vault_key(user_id, key)
    if not vault_key:
        return False
    try:
        from stonepi_vault import get_vault, set_secret
    except ImportError:
        return False
    try:
        if value:
            set_secret(vault_key, value)
        else:
            get_vault().delete(vault_key)
    except Exception:
        return False
    return True


def get_value(db: Session, user_id: int, key: str, default: str = "") -> str:
    if _user_vault_key(user_id, key):
        vaulted = _read_user_vault(user_id, key)
        if vaulted:
            return vaulted
        row = db.get(UserSetting, {"user_id": user_id, "key": key})
        if row is not None and row.value != "":
            return row.value
        return default
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is not None and row.value != "":
        return row.value
    return default


def set_value(db: Session, user_id: int, key: str, value: str) -> None:
    if _user_vault_key(user_id, key) and _write_user_vault(user_id, key, value):
        row = db.get(UserSetting, {"user_id": user_id, "key": key})
        if row is not None:
            db.delete(row)
            db.commit()
        return
    now = datetime.now(timezone.utc)
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is None:
        db.add(UserSetting(user_id=user_id, key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    db.commit()


def clear_value(db: Session, user_id: int, key: str) -> None:
    if _user_vault_key(user_id, key):
        _write_user_vault(user_id, key, "")
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is None:
        return
    db.delete(row)
    db.commit()


def get_with_fallback(
    db: Session,
    user_id: int,
    key: str,
    *,
    instance_fallback_keys: list[str] | None = None,
    default: str = "",
) -> str:
    """Return user setting; if blank, try instance keys in order."""
    value = get_value(db, user_id, key, default="")
    if value.strip():
        return value
    for inst_key in instance_fallback_keys or [key]:
        inst = instance_settings.get_value(db, inst_key)
        if inst.strip():
            return inst
    return default


def _user_secret_source(db: Session, user_id: int, key: str) -> str:
    if _read_user_vault(user_id, key):
        return "vault"
    row = db.get(UserSetting, {"user_id": user_id, "key": key})
    if row is not None and row.value != "":
        return "user"
    return ""


def secret_hint(db: Session, user_id: int, key: str) -> dict:
    value = get_with_fallback(db, user_id, key)
    user_source = _user_secret_source(db, user_id, key)
    source = user_source or instance_settings.get_source(db, key)
    return {
        "set": bool(value),
        "source": source or "default",
        "hint": instance_settings.mask_secret(value) if value else "",
    }


def flag_enabled(db: Session, user_id: int, key: str) -> bool:
    raw = get_with_fallback(db, user_id, key).strip().lower()
    return raw in {"1", "true", "on", "yes"}
