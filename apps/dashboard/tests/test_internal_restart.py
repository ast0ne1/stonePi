"""Signed service restart for the Car Thing panel: only catalog units, only with a valid signature."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal_v2

from app import routes, services
from app.main import app

SECRET = "dash-test-secret"
PATH = routes.RESTART_PATH


def _setup(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(services, "session_secret", lambda: SECRET)
    monkeypatch.setattr(services, "control_unit", lambda unit, action: (calls.append((unit, action)) or (True, "ok")))
    return TestClient(app), calls


def _post(client, payload: dict, *, secret: str = SECRET):
    raw = json.dumps(payload).encode()
    return client.post(
        PATH,
        content=raw,
        headers={"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, raw)},
    )


def test_restart_needs_signature(monkeypatch):
    client, calls = _setup(monkeypatch)
    assert client.post(PATH, json={"unit": "stonepi-newscast"}).status_code == 403
    bad = sign_internal_v2("other-secret", "POST", PATH, b'{"unit":"stonepi-newscast"}')
    assert client.post(PATH, content=b'{"unit":"stonepi-newscast"}', headers={"Content-Type": "application/json", **bad}).status_code == 403
    assert calls == []


def test_restart_catalog_unit(monkeypatch):
    client, calls = _setup(monkeypatch)
    body = _post(client, {"unit": "stonepi-newscast", "source": "carthing"}).json()
    assert body["ok"] and calls == [("stonepi-newscast", "restart")]


def test_restart_rejects_other_units(monkeypatch):
    client, calls = _setup(monkeypatch)
    for unit in ("ssh", "nginx", "stonepi-carthing; reboot", ""):
        response = _post(client, {"unit": unit})
        assert response.status_code == 400
    assert calls == []
