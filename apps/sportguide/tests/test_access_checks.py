"""Permission checks: kick-off alerts follow Auth's roster; refresh fails closed without a secret."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db, routes
from app.services import notify as notify_service
from stonepi_auth.session import PlatformUser

ADMIN = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
SAM = "6fa459ea-ee8a-3ca4-894e-db77e160355e"
GONE = "886313e1-3b8a-5372-9b90-0c9aee199e5d"
SECRET = "sg-access-secret"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sport.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()


@pytest.fixture
def sent(monkeypatch):
    import stonepi_contracts

    events = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: events.append(ev.to_dict()) or True)
    return events


def _serve_roster(monkeypatch, people: list[dict] | None):
    from stonepi_auth import internal

    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    body = None if people is None else {"people": people}
    monkeypatch.setattr(internal, "get_internal_json", lambda *a, **kw: body)


def _watch_arsenal(*keys: str):
    for key in keys:
        db.set_watched_teams(key, ["Arsenal"])
    db.replace_source_listings(
        "wheresthematch",
        [{
            "external_id": "a1",
            "sport": "football",
            "league": "Premier League",
            "title": "Arsenal vs Chelsea",
            "starts_at": (NOW + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "channels": [],
            "source_url": "",
        }],
    )


# -- kick-off alerts ---------------------------------------------------------------


def test_revoked_members_get_no_alerts_but_admin_and_members_do(monkeypatch, sent):
    _serve_roster(monkeypatch, [
        {"id": ADMIN, "is_admin": True, "permissions": {}},
        {"id": JO, "is_admin": False, "permissions": {"sportguide": {"can_refresh": False}}},
        {"id": SAM, "is_admin": False, "permissions": {"newscast": {}}},  # SportGuide removed
        # GONE is missing: disabled or deleted in Auth
    ])
    _watch_arsenal(ADMIN, JO, SAM, GONE)
    totals = notify_service.check_all_watching(now=NOW)
    assert sorted(e["user"] for e in sent) == sorted([ADMIN, JO])
    assert totals["notified"] == 2 and totals["no_access"] == 2 and totals["people"] == 2


def test_direct_emit_is_refused_for_a_revoked_member(monkeypatch, sent):
    _serve_roster(monkeypatch, [{"id": ADMIN, "is_admin": True, "permissions": {}}])
    _watch_arsenal(JO)
    assert notify_service.check_approaching_watched(user_key=JO, now=NOW)["notified"] == 0
    assert sent == []


def test_unknown_roster_keeps_sending(monkeypatch, sent):
    _serve_roster(monkeypatch, None)  # Auth down / unreachable
    _watch_arsenal(ADMIN, JO)
    assert notify_service.check_all_watching(now=NOW)["notified"] == 2
    assert sorted(e["user"] for e in sent) == sorted([ADMIN, JO])


def test_no_secret_keeps_sending(monkeypatch, sent):
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    _watch_arsenal(JO)
    assert notify_service.check_all_watching(now=NOW)["notified"] == 1


def test_shared_list_ignores_roster(monkeypatch, sent):
    _serve_roster(monkeypatch, [{"id": ADMIN, "is_admin": True, "permissions": {}}])
    _watch_arsenal(db.LOCAL_KEY)
    notify_service.check_all_watching(now=NOW)
    assert [e["audience"] for e in sent] == ["household"]


# -- refresh without a session secret ------------------------------------------------


def test_refresh_refused_without_secret_outside_dev(monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "_auth_optional", lambda: False)
    assert routes._can_refresh(None) is False


def test_refresh_allowed_without_secret_in_dev(monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "_auth_optional", lambda: True)
    assert routes._can_refresh(None) is True


def test_auth_optional_follows_stonepi_dev(monkeypatch):
    monkeypatch.setattr(routes.os, "name", "posix")
    monkeypatch.delenv("STONEPI_DEV", raising=False)
    assert routes._auth_optional() is False
    monkeypatch.setenv("STONEPI_DEV", "1")
    assert routes._auth_optional() is True


def test_refresh_post_refused_without_secret_outside_dev(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "_auth_optional", lambda: False)
    app = FastAPI()
    app.include_router(routes.router)
    assert TestClient(app).post("/sources/refresh", data={"csrf_token": "x"}).status_code == 403


def test_refresh_with_secret_still_uses_capability(monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    member = PlatformUser(user_id=JO, username="jo", display_name="Jo", is_admin=False, apps=["sportguide"])
    assert routes._can_refresh(member) is False
    assert routes._can_refresh(None) is False
