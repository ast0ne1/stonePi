from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting, UserSetting, utcnow

DEFAULT_PALETTE = "default"
DEFAULT_THEME = "system"

WEEKDAYS = [
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]
WEEKDAY_CODES = [code for code, _ in WEEKDAYS]
WEEKDAY_LABELS = dict(WEEKDAYS)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def get_value(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def set_value(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def get_user_value(db: Session, user_id: int, key: str, default: str = "") -> str:
    row = db.get(UserSetting, (user_id, key))
    return row.value if row else default


def set_user_value(db: Session, user_id: int, key: str, value: str) -> None:
    row = db.get(UserSetting, (user_id, key))
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value))
    db.commit()


def https_enabled(db: Session) -> bool:
    return get_value(db, "https_enabled", "0") in ("1", "true", "yes")


def set_https_enabled(db: Session, enabled: bool) -> None:
    set_value(db, "https_enabled", "1" if enabled else "0")


def get_global_schedule(db: Session, default_minutes: int) -> dict:
    raw = get_value(db, "global_schedule", "")
    if raw:
        try:
            config = json.loads(raw)
        except (ValueError, TypeError):
            config = None
        if isinstance(config, dict):
            if config.get("mode") == "weekly" and config.get("days") and config.get("times"):
                days = [d for d in config["days"] if d in WEEKDAY_CODES]
                times = [t for t in config["times"] if _TIME_RE.match(t)]
                if days and times:
                    return {"mode": "weekly", "days": days, "times": sorted(times)}
            elif config.get("mode") == "interval" and isinstance(config.get("interval_minutes"), int):
                return {"mode": "interval", "interval_minutes": max(15, config["interval_minutes"])}
    return {"mode": "interval", "interval_minutes": max(15, default_minutes)}


def set_global_schedule(db: Session, config: dict) -> None:
    set_value(db, "global_schedule", json.dumps(config))


def _clear_setting(db: Session, key: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        return
    db.delete(row)
    db.commit()


def get_brightdata_api_key(db: Session) -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("BRIGHTDATA_API_KEY", env_name="BRIGHTDATA_API_KEY", default="")
        if vaulted:
            return vaulted
    except Exception:
        pass
    return get_value(db, "brightdata_api_key", "")


def set_brightdata_api_key(db: Session, api_key: str) -> None:
    value = api_key.strip()
    try:
        from stonepi_vault import get_vault, set_secret

        if value:
            set_secret("BRIGHTDATA_API_KEY", value)
        else:
            get_vault().delete("BRIGHTDATA_API_KEY")
        _clear_setting(db, "brightdata_api_key")
        return
    except Exception:
        pass
    set_value(db, "brightdata_api_key", value)


def get_google_oauth_credentials(db: Session) -> tuple[str, str]:
    """DB-stored Google OAuth client id/secret, set from Settings > Calendar
    Integrations. Falls back to Vault, then .env (GOOGLE_CLIENT_ID/SECRET)."""
    from app.config import env

    try:
        from stonepi_vault import get_secret

        vault_id = get_secret("GOOGLE_CLIENT_ID", env_name="GOOGLE_CLIENT_ID", default="")
        vault_secret = get_secret("GOOGLE_CLIENT_SECRET", env_name="GOOGLE_CLIENT_SECRET", default="")
    except Exception:
        vault_id, vault_secret = "", ""

    db_id = get_value(db, "google_client_id", "")
    db_secret = get_value(db, "google_client_secret", "")
    client_id = vault_id or db_id or env.google_client_id
    client_secret = vault_secret or db_secret or env.google_client_secret
    return client_id, client_secret


def set_google_oauth_credentials(db: Session, client_id: str, client_secret: str) -> None:
    cid = client_id.strip()
    secret = client_secret.strip()
    try:
        from stonepi_vault import get_vault, set_secret

        if cid:
            set_secret("GOOGLE_CLIENT_ID", cid)
        else:
            get_vault().delete("GOOGLE_CLIENT_ID")
        if secret:
            set_secret("GOOGLE_CLIENT_SECRET", secret)
        else:
            get_vault().delete("GOOGLE_CLIENT_SECRET")
        _clear_setting(db, "google_client_id")
        _clear_setting(db, "google_client_secret")
        return
    except Exception:
        pass
    set_value(db, "google_client_id", cid)
    set_value(db, "google_client_secret", secret)


def get_global_keywords(db: Session) -> tuple[str, str]:
    """Global keyword include/exclude, applied on top of each source's own
    (a source's events must pass both to be kept)."""
    return get_value(db, "global_keyword_include", ""), get_value(db, "global_keyword_exclude", "")


def set_global_keywords(db: Session, include: str, exclude: str) -> None:
    set_value(db, "global_keyword_include", include.strip())
    set_value(db, "global_keyword_exclude", exclude.strip())


def describe_schedule(config: dict) -> str:
    if config.get("mode") == "weekly":
        days = ", ".join(WEEKDAY_LABELS.get(d, d) for d in config.get("days", []))
        times = ", ".join(config.get("times", []))
        return f"{days} at {times}"
    minutes = config.get("interval_minutes", 60)
    if minutes % 1440 == 0:
        return f"Every {minutes // 1440}d"
    if minutes % 60 == 0:
        return f"Every {minutes // 60}h"
    return f"Every {minutes}m"
