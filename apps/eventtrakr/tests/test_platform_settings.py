from types import SimpleNamespace

from app.db import SessionLocal, init_db
from app.models import User
from app.routes.settings import SETTINGS_GROUPS, SETTINGS_TABS, _settings_groups_for, _settings_tabs_for
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


def test_settings_groups_cover_visible_tabs(monkeypatch):
    monkeypatch.setattr("app.routes.settings.env.stonepi_session_secret", "")
    admin = SimpleNamespace(role="admin")
    grouped = {
        key
        for _gid, _label, rows in _settings_groups_for(admin)
        for key, _tab_label, _sub in rows
    }
    assert grouped == {key for key, _ in SETTINGS_TABS}
    assert len(SETTINGS_GROUPS) == 3


def test_settings_groups_hide_platform_tabs(monkeypatch):
    monkeypatch.setattr("app.routes.settings.env.stonepi_session_secret", "platform-secret")
    admin = SimpleNamespace(role="admin")
    grouped = {
        key
        for _gid, _label, rows in _settings_groups_for(admin)
        for key, _tab_label, _sub in rows
    }
    assert "users" not in grouped
    assert "update" not in grouped
    assert "general" in grouped


def test_factory_admin_suppressed_under_sso(monkeypatch):
    monkeypatch.setattr("app.services.users.env.stonepi_session_secret", "platform-secret")
    assert users_svc.using_factory_admin() is False


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
