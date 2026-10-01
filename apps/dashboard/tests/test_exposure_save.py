"""Settings → Network → exposure: a flag file Dashboard can't write must not be a 500."""

from __future__ import annotations

from fastapi.testclient import TestClient
from stonepi_auth.session import PlatformUser

from app import routes
from app.main import app

ADMIN = PlatformUser(user_id="1", username="admin", display_name="Admin", is_admin=True, apps=[])


def _client(monkeypatch):
    monkeypatch.setattr(routes, "_user_or_login", lambda request, **kw: (ADMIN, None))
    monkeypatch.setattr(routes, "_require_csrf", lambda request, form: True)
    return TestClient(app)


def test_exposure_permission_denied_shows_message(monkeypatch):
    import stonepi_auth

    def denied(mode):
        raise PermissionError(13, "Permission denied", "/var/lib/stonepi/exposure")

    monkeypatch.setattr(stonepi_auth, "set_exposure_mode", denied)
    resp = _client(monkeypatch).post("/settings/network/exposure", data={"exposure": "public"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "err=" in resp.headers["location"] and "permission" in resp.headers["location"].lower()


def test_exposure_saves(monkeypatch):
    import stonepi_auth

    saved = []
    monkeypatch.setattr(stonepi_auth, "set_exposure_mode", lambda mode: saved.append(mode))
    resp = _client(monkeypatch).post("/settings/network/exposure", data={"exposure": "public"}, follow_redirects=False)
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    assert saved == ["public"]
