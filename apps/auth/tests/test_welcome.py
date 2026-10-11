"""Home welcome message preference (F6): options, default, 60-character cap, API."""

from __future__ import annotations

import os
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="auth-welcome-test-")
os.environ["STONEPI_SESSION_SECRET"] = "test-secret"
os.environ["STONEPI_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/users.sqlite"
os.environ["STONEPI_VAULT_DIR"] = os.path.join(_TMP, "vault")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import users as users_svc  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from stonepi_auth.session import CSRF_COOKIE  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _tables():
    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _name() -> str:
    return f"w{uuid.uuid4().hex[:10]}"


def test_normalize_welcome_options_default_and_cap():
    assert users_svc.normalize_welcome(None) == {"mode": "time", "text": ""}
    assert users_svc.normalize_welcome("bogus")["mode"] == "time"
    for mode in ("time", "hello", "none"):
        assert users_svc.normalize_welcome(mode)["mode"] == mode
    long = "x" * 80
    assert users_svc.normalize_welcome("custom", long) == {"mode": "custom", "text": "x" * 60}
    # Whitespace collapses; an empty custom text falls back to the default.
    assert users_svc.normalize_welcome("custom", "  Hi   {name} ")["text"] == "Hi {name}"
    assert users_svc.normalize_welcome("custom", "   ")["mode"] == "time"


def test_welcome_defaults_to_time_and_saves_per_person():
    db = SessionLocal()
    one = users_svc.create_user(db, username=_name(), password="password123")
    two = users_svc.create_user(db, username=_name(), password="password123")
    assert users_svc.welcome(db, one) == {"mode": "time", "text": ""}
    users_svc.set_welcome(db, one, "custom", "Morning, {name}")
    assert users_svc.welcome(db, one) == {"mode": "custom", "text": "Morning, {name}"}
    assert users_svc.welcome(db, two)["mode"] == "time"
    assert users_svc.user_payload(one, db)["welcome"]["mode"] == "custom"
    db.close()


def test_api_put_welcome(client):
    db = SessionLocal()
    name = _name()
    users_svc.create_user(db, username=name, password="password123")
    db.close()

    client.cookies.clear()
    page = client.get("/login")
    csrf = page.cookies.get(CSRF_COOKIE) or client.cookies.get(CSRF_COOKIE) or ""
    client.post("/login", data={"username": name, "password": "password123", "csrf_token": csrf, "next": "/"}, follow_redirects=False)
    csrf = client.cookies.get(CSRF_COOKIE)

    body = client.put("/api/me/welcome", json={"mode": "hello"}, headers={"X-StonePi-CSRF": csrf}).json()
    assert body["welcome"] == {"mode": "hello", "text": ""}
    assert client.get("/api/me").json()["welcome"]["mode"] == "hello"
    client.cookies.clear()
    assert client.put("/api/me/welcome", json={"mode": "none"}).status_code == 401


def test_set_own_alias(client):
    db = SessionLocal()
    name = _name()
    users_svc.create_user(db, username=name, password="password123", display_name="Admin")
    db.close()
    client.cookies.clear()
    page = client.get("/login")
    csrf = page.cookies.get(CSRF_COOKIE) or client.cookies.get(CSRF_COOKIE) or ""
    client.post("/login", data={"username": name, "password": "password123", "csrf_token": csrf, "next": "/"}, follow_redirects=False)
    headers = {"X-StonePi-CSRF": client.cookies.get(CSRF_COOKIE)}
    body = client.patch("/api/me", json={"display_name": "  Adam  "}, headers=headers).json()
    assert body["display_name"] == "Adam"
    assert client.get("/api/me").json()["display_name"] == "Adam"
    assert client.patch("/api/me", json={"display_name": "x" * 90}, headers=headers).json()["display_name"] == "x" * 40
    client.cookies.clear()
