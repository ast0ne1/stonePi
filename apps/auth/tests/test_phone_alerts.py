"""Platform Phone alerts permission: schema migration, API, session, roster."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid

_TMP = tempfile.mkdtemp(prefix="auth-test-")
os.environ["STONEPI_SESSION_SECRET"] = "test-secret"
os.environ["STONEPI_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/users.sqlite"
os.environ["STONEPI_VAULT_DIR"] = os.path.join(_TMP, "vault")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import users as users_svc  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from stonepi_auth.internal import sign_internal  # noqa: E402
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, decode_session  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _tables():
    init_db()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _name() -> str:
    return f"u{uuid.uuid4().hex[:10]}"


def test_migration_adds_column_and_copies_newscast_capability(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80), display_name VARCHAR(120),
            password_hash TEXT, enabled BOOLEAN, is_admin BOOLEAN, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE app_grants (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), app_id VARCHAR(40),
            capabilities TEXT DEFAULT '{}');
        INSERT INTO users (id, username, enabled, is_admin) VALUES ('a', 'alice', 1, 0), ('b', 'bob', 1, 0);
        """
    )
    con.execute("INSERT INTO app_grants VALUES ('g1', 'a', 'newscast', ?)", (json.dumps({"can_use_ntfy": True}),))
    con.execute("INSERT INTO app_grants VALUES ('g2', 'b', 'newscast', ?)", (json.dumps({"can_use_ntfy": False}),))
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{path}", future=True)
    db = sessionmaker(bind=engine, future=True)()
    users_svc.ensure_schema(db)
    users_svc.ensure_schema(db)  # idempotent: a second run must not re-copy or fail
    db.close()
    engine.dispose()

    con = sqlite3.connect(path)
    rows = dict(con.execute("SELECT username, phone_alerts FROM users").fetchall())
    con.close()
    assert rows == {"alice": 1, "bob": 0}


def test_admins_always_have_phone_alerts():
    db = SessionLocal()
    admin = users_svc.create_user(db, username=_name(), password="password123", is_admin=True)
    assert users_svc.phone_alerts_allowed(admin) is True
    assert users_svc.user_payload(admin, db)["phone_alerts"] is True
    db.close()


def test_create_and_update_phone_alerts():
    db = SessionLocal()
    person = users_svc.create_user(db, username=_name(), password="password123", apps=["newscast"])
    assert users_svc.phone_alerts_allowed(person) is False
    person = users_svc.update_user(db, person, phone_alerts=True)
    assert users_svc.phone_alerts_allowed(person) is True
    # Leaving phone_alerts out of an update keeps it.
    person = users_svc.update_user(db, person, display_name="Someone")
    assert person.phone_alerts is True
    # NewsCast no longer has its own phone-alerts capability (Phase 9).
    assert "can_use_ntfy" not in users_svc.granted_permissions(person).get("newscast", {})
    person = users_svc.update_user(db, person, phone_alerts=False)
    assert users_svc.phone_alerts_allowed(person) is False
    db.close()


def test_roster_reflects_permission(client):
    db = SessionLocal()
    on = users_svc.create_user(db, username=_name(), password="password123", phone_alerts=True)
    off = users_svc.create_user(db, username=_name(), password="password123")
    on_id, off_id = on.id, off.id
    db.close()
    path = "/api/internal/people"
    rows = {r["id"]: r for r in client.get(path, headers=sign_internal("test-secret", "GET", path)).json()["people"]}
    assert rows[on_id]["phone_alerts"] is True
    assert rows[off_id]["phone_alerts"] is False


def _login(client, username: str) -> None:
    client.cookies.clear()
    page = client.get("/login")
    csrf = page.cookies.get(CSRF_COOKIE) or client.cookies.get(CSRF_COOKIE) or ""
    client.post(
        "/login",
        data={"username": username, "password": "password123", "csrf_token": csrf, "next": "/"},
        follow_redirects=False,
    )


def test_api_patch_and_session_flag(client):
    db = SessionLocal()
    admin_name, person_name = _name(), _name()
    users_svc.create_user(db, username=admin_name, password="password123", is_admin=True)
    person = users_svc.create_user(db, username=person_name, password="password123")
    person_id = person.id
    db.close()

    _login(client, admin_name)
    csrf = client.cookies.get(CSRF_COOKIE)
    assert client.cookies.get(COOKIE_NAME), "admin login failed"
    body = client.patch(
        f"/api/users/{person_id}", json={"phone_alerts": True}, headers={"X-StonePi-CSRF": csrf}
    ).json()
    assert body["phone_alerts"] is True

    _login(client, person_name)
    session = decode_session(client.cookies.get(COOKIE_NAME), users_svc.session_secret())
    assert session is not None and session.phone_alerts is True
    client.cookies.clear()


def test_catalog_hides_notify_and_drops_newscast_phone_alerts():
    from stonepi_auth import APP_CATALOG

    by_id = {a["id"]: a for a in APP_CATALOG}
    assert by_id["notify"].get("grants") is False
    assert "can_use_ntfy" not in {c["id"] for c in by_id["newscast"]["capabilities"]}


def test_old_cookie_without_flag_falls_back(monkeypatch):
    from stonepi_auth.alerts import phone_alerts_allowed
    from stonepi_auth.session import PlatformUser

    legacy = PlatformUser(
        user_id="x", username="x", display_name="x", is_admin=False, apps=["newscast"],
        permissions={"newscast": {"can_use_ntfy": True}}, exp=int(time.time()) + 60,
    )
    assert legacy.phone_alerts is None
    assert phone_alerts_allowed(legacy) is True
    legacy.phone_alerts = False
    assert phone_alerts_allowed(legacy) is False


def test_names_endpoint_is_signed_and_separate(client):
    db = SessionLocal()
    person = users_svc.create_user(db, username=_name(), password="password123", display_name="Jo Bloggs")
    gone = users_svc.create_user(db, username=_name(), password="password123", enabled=False)
    person_id, gone_id = person.id, gone.id
    db.close()
    path = "/api/internal/people/names"
    assert client.get(path).status_code == 404
    rows = {r["id"]: r for r in client.get(path, headers=sign_internal("test-secret", "GET", path)).json()["people"]}
    assert rows[person_id] == {"id": person_id, "name": "Jo Bloggs"}
    assert gone_id not in rows
    # The platform roster carries no names (permissions let EventTrakr revoke paid polling).
    roster_path = "/api/internal/people"
    roster = client.get(roster_path, headers=sign_internal("test-secret", "GET", roster_path)).json()["people"]
    assert all(set(r) == {"id", "is_admin", "phone_alerts", "permissions"} for r in roster)
    # A signature for the roster path doesn't open the names path.
    assert client.get(path, headers=sign_internal("test-secret", "GET", roster_path)).status_code == 404
