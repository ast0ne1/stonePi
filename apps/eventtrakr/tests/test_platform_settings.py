from types import SimpleNamespace

from app.db import SessionLocal, init_db
from app.models import User
from app.routes.settings import _settings_tabs_for
from app.services import users as users_svc
from sqlalchemy import select


def test_settings_tabs_hide_when_platform_secret(monkeypatch):
    monkeypatch.setattr("app.routes.settings.env.stonepi_session_secret", "platform-secret")
    admin = SimpleNamespace(role="admin")
    keys = {key for key, _ in _settings_tabs_for(admin)}
    assert "users" not in keys
    assert "update" not in keys
    assert "general" in keys


def test_settings_tabs_keep_users_when_solo(monkeypatch):
    monkeypatch.setattr("app.routes.settings.env.stonepi_session_secret", "")
    admin = SimpleNamespace(role="admin")
    keys = {key for key, _ in _settings_tabs_for(admin)}
    assert "users" in keys
    assert "update" in keys


def test_platform_role_sync_on_relogin():
    init_db()
    auth_id = "auth-et-role-sync-test"
    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.auth_user_id == auth_id)).scalar_one_or_none()
        if existing is not None:
            db.delete(existing)
            db.commit()

    platform = SimpleNamespace(user_id=auth_id, username="pat-role-sync", is_admin=False)
    user = users_svc.get_or_create_from_platform(platform)
    assert user.role == "user"
    platform.is_admin = True
    again = users_svc.get_or_create_from_platform(platform)
    assert again.role == "admin"
    with SessionLocal() as db:
        row = db.execute(select(User).where(User.auth_user_id == auth_id)).scalar_one()
        assert row.role == "admin"
        db.delete(row)
        db.commit()
