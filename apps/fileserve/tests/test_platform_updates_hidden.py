"""Under StonePi, updates live in Dashboard → Settings → Updates: FileServe hides its own updater."""

from __future__ import annotations

import pytest

from app.services import update
from test_personal_alerts import client  # noqa: F401  (StonePi-signed-in admin client)

NEWER = {"newer": True, "valid": True, "tag": "v9.9.9", "version": "9.9.9", "message": ""}


@pytest.fixture
def pending_update(monkeypatch):
    monkeypatch.setattr(update, "last_check", lambda db: dict(NEWER))

    def refuse(*args, **kwargs):
        raise AssertionError("in-app updater ran under StonePi")

    monkeypatch.setattr(update, "check_latest", refuse)
    monkeypatch.setattr(update, "install_latest", refuse)
    monkeypatch.setattr(update, "rollback_code", refuse)


def test_settings_hide_updater_under_stonepi(client, pending_update):  # noqa: F811
    backup = client.get("/admin/settings?tab=backup").get_data(as_text=True)
    assert "Roll back last app" not in backup
    assert 'id="settings-panel-update"' not in backup
    assert 'id="settings-panel-update-form"' not in backup
    assert "v9.9.9" not in backup


def test_update_routes_refuse_under_stonepi(client, pending_update):  # noqa: F811
    for action in ("check", "install", "rollback"):
        response = client.post(f"/admin/settings/updates/{action}")
        assert response.status_code in (302, 303, 400, 403), action
