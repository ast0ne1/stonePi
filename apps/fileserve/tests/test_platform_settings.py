from types import SimpleNamespace

from app.main import normalize_settings_tab, settings_tabs_for
from app.services import users as users_svc


def test_platform_managed_hides_users_and_update():
    solo = {key for key, _ in settings_tabs_for(admin=True, platform_managed=False)}
    platform = {key for key, _ in settings_tabs_for(admin=True, platform_managed=True)}
    assert "users" in solo and "update" in solo
    assert "users" not in platform and "update" not in platform
    assert "backup" in platform
    assert normalize_settings_tab("users", admin=True, platform_managed=True) == "device"
    assert normalize_settings_tab("update", admin=True, platform_managed=True) == "device"


def test_platform_role_sync_on_relogin(tmp_path, monkeypatch):
    hosted = tmp_path / "hosted"
    hosted.mkdir()
    monkeypatch.setattr("app.services.users.HOSTED_DIR", hosted)
    from app.db import init_db
    from app import db as database

    init_db(f"sqlite:///{(tmp_path / 'users.db').as_posix()}")
    db = database.SessionLocal()
    try:
        platform = SimpleNamespace(user_id="auth-1", username="jane", is_admin=False)
        user = users_svc.get_or_create_from_platform(db, platform)
        assert user.role == "user"
        platform.is_admin = True
        again = users_svc.get_or_create_from_platform(db, platform)
        assert again.id == user.id
        assert again.role == "admin"
        platform.is_admin = False
        demoted = users_svc.get_or_create_from_platform(db, platform)
        assert demoted.role == "user"
    finally:
        db.close()
