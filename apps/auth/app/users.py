from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import passwords
from app.config import env
from app.models import AppGrant, AuthSession, PlatformSetting, User, utcnow
from stonepi_auth import APP_IDS, APP_CATALOG, capabilities_for

USERNAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
COOKIE_MAX_AGE = 60 * 60 * 24 * 14
DISABLED_APPS_KEY = "disabled_apps"
LAUNCHER_ORDER_PREFIX = "launcher_order:"


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def validate_username(value: str) -> str:
    name = normalize_username(value)
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("Username must be 2–32 characters: start with a letter, then letters, digits, or hyphens.")
    return name


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    if env.session_secret.strip():
        return env.session_secret.strip()
    from app.config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "session.secret"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    value = secrets.token_hex(32)
    path.write_text(value, encoding="utf-8")
    return value


def ensure_schema(db: Session) -> None:
    """Lightweight migrations for existing SQLite installs."""
    try:
        cols = {row[1] for row in db.execute(text("PRAGMA table_info(app_grants)")).all()}
        if "capabilities" not in cols:
            db.execute(text("ALTER TABLE app_grants ADD COLUMN capabilities TEXT DEFAULT '{}'"))
            db.commit()
    except Exception:
        db.rollback()
    try:
        db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS platform_settings ("
                "key VARCHAR(80) PRIMARY KEY, value TEXT DEFAULT '')"
            )
        )
        db.commit()
    except Exception:
        db.rollback()


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(PlatformSetting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def disabled_apps(db: Session) -> list[str]:
    raw = get_setting(db, DISABLED_APPS_KEY, "[]")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item) in APP_IDS]


def set_disabled_apps(db: Session, app_ids: Iterable[str]) -> list[str]:
    wanted = sorted({item for item in app_ids if item in APP_IDS and item != "dashboard"})
    set_setting(db, DISABLED_APPS_KEY, json.dumps(wanted))
    return wanted


def _launcher_order_key(user_id: str) -> str:
    return f"{LAUNCHER_ORDER_PREFIX}{user_id}"


def normalize_launcher_order(app_ids: Iterable[str]) -> list[str]:
    from stonepi_auth import LAUNCHER_APP_IDS

    allowed = set(LAUNCHER_APP_IDS)
    seen: set[str] = set()
    ordered: list[str] = []
    for app_id in app_ids:
        value = str(app_id or "").strip()
        if value in allowed and value not in seen:
            ordered.append(value)
            seen.add(value)
    for app_id in LAUNCHER_APP_IDS:
        if app_id not in seen:
            ordered.append(app_id)
    return ordered


def launcher_order(db: Session, user: User) -> list[str]:
    from stonepi_auth import LAUNCHER_APP_IDS

    raw = get_setting(db, _launcher_order_key(user.id), "")
    if not raw.strip():
        return list(LAUNCHER_APP_IDS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return list(LAUNCHER_APP_IDS)
    if not isinstance(data, list):
        return list(LAUNCHER_APP_IDS)
    return normalize_launcher_order(data)


def set_launcher_order(db: Session, user: User, app_ids: Iterable[str]) -> list[str]:
    ordered = normalize_launcher_order(app_ids)
    set_setting(db, _launcher_order_key(user.id), json.dumps(ordered))
    return ordered


def enabled_app_ids(db: Session) -> list[str]:
    disabled = set(disabled_apps(db))
    return [app_id for app_id in APP_IDS if app_id not in disabled]


def ensure_admin_user(db: Session) -> User:
    ensure_schema(db)
    existing = db.execute(select(User).where(User.is_admin.is_(True))).scalars().first()
    if existing is not None:
        # Backfill grants when new catalog apps appear (e.g. Pinboard, Studio).
        set_grants(db, existing, enabled_app_ids(db))
        db.commit()
        db.refresh(existing)
        return existing
    username = validate_username(env.admin_username or "admin")
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        display_name=env.admin_display_name or "Admin",
        password_hash=passwords.hash_password(env.admin_password or "admin"),
        enabled=True,
        is_admin=True,
    )
    db.add(user)
    for app_id in APP_IDS:
        db.add(AppGrant(id=str(uuid.uuid4()), user_id=user.id, app_id=app_id, capabilities="{}"))
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def get_by_username(db: Session, username: str) -> User | None:
    name = normalize_username(username)
    return db.execute(select(User).where(User.username == name)).scalars().first()


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.username.asc())).scalars())


def _parse_caps(raw: str | None) -> dict[str, bool]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def granted_apps(user: User, *, enabled_only: list[str] | None = None) -> list[str]:
    # Admins always receive every platform-enabled app so catalog additions
    # appear after upgrade without a manual Users save.
    if user.is_admin:
        if enabled_only is not None:
            return list(enabled_only)
        return list(APP_IDS)
    apps = sorted({grant.app_id for grant in user.grants})
    if enabled_only is not None:
        allow = set(enabled_only)
        apps = [app_id for app_id in apps if app_id in allow]
    return apps


def granted_permissions(user: User, *, enabled_only: list[str] | None = None) -> dict[str, dict[str, bool]]:
    allow = set(enabled_only) if enabled_only is not None else None
    out: dict[str, dict[str, bool]] = {}
    for grant in user.grants:
        if allow is not None and grant.app_id not in allow:
            continue
        caps = _parse_caps(grant.capabilities)
        known = {item["id"] for item in capabilities_for(grant.app_id)}
        if user.is_admin:
            out[grant.app_id] = {cap_id: True for cap_id in known}
        else:
            out[grant.app_id] = {cap_id: bool(caps.get(cap_id)) for cap_id in known}
    return out


def set_grants(
    db: Session,
    user: User,
    app_ids: Iterable[str],
    permissions: dict[str, dict[str, bool]] | None = None,
) -> None:
    permissions = permissions or {}
    enabled = set(enabled_app_ids(db))
    wanted = {item for item in app_ids if item in APP_IDS and item in enabled}
    if user.is_admin:
        wanted = set(enabled)
    existing = {grant.app_id: grant for grant in user.grants}
    for app_id, grant in list(existing.items()):
        if app_id not in wanted:
            db.delete(grant)
    for app_id in wanted:
        known = {item["id"] for item in capabilities_for(app_id)}
        caps = permissions.get(app_id) or {}
        if user.is_admin:
            payload = {cap_id: True for cap_id in known}
        else:
            payload = {cap_id: bool(caps.get(cap_id)) for cap_id in known}
        encoded = json.dumps(payload)
        if app_id in existing:
            existing[app_id].capabilities = encoded
        else:
            db.add(AppGrant(id=str(uuid.uuid4()), user_id=user.id, app_id=app_id, capabilities=encoded))


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str = "",
    is_admin: bool = False,
    enabled: bool = True,
    apps: Iterable[str] | None = None,
    permissions: dict[str, dict[str, bool]] | None = None,
) -> User:
    name = validate_username(username)
    if get_by_username(db, name) is not None:
        raise ValueError("That username is already taken.")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    user = User(
        id=str(uuid.uuid4()),
        username=name,
        display_name=(display_name or name).strip(),
        password_hash=passwords.hash_password(password),
        enabled=enabled,
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    set_grants(db, user, apps if apps is not None else enabled_app_ids(db), permissions)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    user: User,
    *,
    display_name: str | None = None,
    enabled: bool | None = None,
    is_admin: bool | None = None,
    apps: Iterable[str] | None = None,
    permissions: dict[str, dict[str, bool]] | None = None,
    password: str | None = None,
) -> User:
    if display_name is not None:
        user.display_name = display_name.strip() or user.username
    if enabled is not None:
        if user.is_admin and not enabled:
            others = db.execute(
                select(User).where(User.is_admin.is_(True), User.enabled.is_(True), User.id != user.id)
            ).scalars().first()
            if others is None:
                raise ValueError("Keep at least one enabled administrator.")
        user.enabled = bool(enabled)
        if not user.enabled:
            revoke_sessions(db, user.id)
    if is_admin is not None:
        user.is_admin = bool(is_admin)
    if password:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        user.password_hash = passwords.hash_password(password)
        revoke_sessions(db, user.id)
    if apps is not None or permissions is not None:
        current_apps = apps if apps is not None else granted_apps(user)
        set_grants(db, user, current_apps, permissions)
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User) -> None:
    if user.is_admin:
        others = db.execute(
            select(User).where(User.is_admin.is_(True), User.enabled.is_(True), User.id != user.id)
        ).scalars().first()
        if others is None:
            raise ValueError("Keep at least one administrator.")
    db.delete(user)
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_by_username(db, username)
    if user is None or not user.enabled:
        return None
    if not passwords.verify_password(user.password_hash, password):
        return None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
        db.commit()
    return user


def create_session(db: Session, user: User) -> AuthSession:
    now = datetime.now(timezone.utc)
    session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(seconds=COOKIE_MAX_AGE),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: str) -> AuthSession | None:
    if not session_id:
        return None
    row = db.get(AuthSession, session_id)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return row


def revoke_session(db: Session, session_id: str) -> None:
    row = db.get(AuthSession, session_id)
    if row is not None:
        db.delete(row)
        db.commit()


def revoke_sessions(db: Session, user_id: str, *, keep_session_id: str | None = None) -> None:
    rows = db.execute(select(AuthSession).where(AuthSession.user_id == user_id)).scalars().all()
    for row in rows:
        if keep_session_id and row.id == keep_session_id:
            continue
        db.delete(row)
    db.commit()


def change_own_password(
    db: Session,
    user: User,
    *,
    current_password: str,
    new_password: str,
    keep_session_id: str | None = None,
) -> User:
    """Let a signed-in user set a new password after proving the current one."""
    if not passwords.verify_password(user.password_hash, current_password or ""):
        raise ValueError("Current password is not right.")
    if len(new_password or "") < 8:
        raise ValueError("Password must be at least 8 characters.")
    if current_password == new_password:
        raise ValueError("Choose a different password.")
    user.password_hash = passwords.hash_password(new_password)
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    revoke_sessions(db, user.id, keep_session_id=keep_session_id)
    return user


def using_factory_admin(db: Session) -> bool:
    """True when the first admin account still accepts the factory password."""
    admin = (
        db.execute(select(User).where(User.is_admin.is_(True)).order_by(User.created_at.asc()))
        .scalars()
        .first()
    )
    if admin is None:
        return False
    factory = (env.admin_password or "admin").strip() or "admin"
    return passwords.verify_password(admin.password_hash, factory)


def user_payload(user: User, db: Session | None = None) -> dict:
    enabled = enabled_app_ids(db) if db is not None else list(APP_IDS)
    payload = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "enabled": user.enabled,
        "is_admin": user.is_admin,
        "apps": granted_apps(user, enabled_only=enabled),
        "permissions": granted_permissions(user, enabled_only=enabled),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }
    if db is not None:
        payload["launcher_order"] = launcher_order(db, user)
    return payload


def catalog_payload(db: Session) -> list[dict]:
    disabled = set(disabled_apps(db))
    out = []
    for item in APP_CATALOG:
        out.append({**item, "enabled": item["id"] not in disabled})
    return out
