"""Under StonePi, updates live in Dashboard → Settings → Updates: NewsCast hides its own updater."""

from __future__ import annotations

import pytest

from app.routers import ui
from test_reader_tabs import ctx  # noqa: F401  (StonePi-signed-in admin client)

NEWER = {"newer": True, "valid": True, "tag": "v9.9.9", "version": "9.9.9", "message": ""}


@pytest.fixture
def pending_update(monkeypatch):
    monkeypatch.setattr(ui.update, "last_check", lambda db: dict(NEWER))

    def refuse(*args, **kwargs):
        raise AssertionError("in-app updater ran under StonePi")

    monkeypatch.setattr(ui.update, "check_latest", refuse)
    monkeypatch.setattr(ui.update, "install_latest", refuse)
    monkeypatch.setattr(ui.update, "rollback_code", refuse)


def test_settings_hide_updater_under_stonepi(ctx, pending_update):  # noqa: F811
    client, _db, _user, _tmp = ctx
    backup = client.get("/settings?tab=backup").text
    assert "Roll back last app" not in backup
    assert 'id="settings-panel-update"' not in backup
    assert 'name="github_repo"' not in backup
    # The old Update tab link falls back to a visible tab instead of the updater.
    assert 'id="settings-panel-update"' not in client.get("/settings?tab=update").text


def test_status_has_no_update_banner_under_stonepi(ctx, pending_update):  # noqa: F811
    client, _db, _user, _tmp = ctx
    for path in ("/device?tab=status", "/status"):
        response = client.get(path)
        if response.status_code == 200:
            assert "v9.9.9" not in response.text


def test_update_routes_refuse_under_stonepi(ctx, pending_update):  # noqa: F811
    client, _db, _user, _tmp = ctx
    for action in ("check", "install", "rollback"):
        response = client.post(f"/settings/updates/{action}")
        assert response.status_code in (302, 303, 403), action
