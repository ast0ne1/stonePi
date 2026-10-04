"""Shared Auth roster helper: caching, resolution and fallback semantics."""

from __future__ import annotations

import pytest

from stonepi_auth import internal, roster as roster_mod
from stonepi_auth.internal import SIG_HEADER, TS_HEADER, verify_internal
from stonepi_auth.roster import MemberAccess, Roster, get_roster, member_access, reset_rosters

SECRET = "roster-secret"
APP = "eventtrakr"  # has catalog capabilities with mixed defaults

PEOPLE = [
    {"id": "member", "is_admin": False, "phone_alerts": False,
     "permissions": {APP: {"can_manage_sources": False, "can_use_social": True}}},
    {"id": "defaults", "is_admin": False, "phone_alerts": False, "permissions": {APP: {}}},
    {"id": "no-app", "is_admin": False, "phone_alerts": False, "permissions": {"newscast": {}}},
    {"id": "admin", "is_admin": True, "phone_alerts": True, "permissions": {}},
]


class FakeAuth:
    def __init__(self, body=None):
        self.body = {"people": PEOPLE} if body is None else body
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False

    def __call__(self, base, secret, path, **kw):
        headers = internal.sign_internal(secret, "GET", path)
        self.calls.append((base, path, headers))
        if self.down:
            return None
        return self.body


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def auth(monkeypatch):
    fake = FakeAuth()
    monkeypatch.setattr(internal, "get_internal_json", fake)
    return fake


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(roster_mod.time, "monotonic", c)
    return c


def make(**kw) -> Roster:
    return Roster(APP, kw.pop("auth_url", "http://auth.test:8011"), kw.pop("secret_getter", lambda: SECRET), **kw)


def test_none_when_never_fetched(monkeypatch):
    monkeypatch.setattr(internal, "get_internal_json", lambda *a, **k: None)
    r = make()
    assert r.refresh() is False
    assert r.access("member") is None
    assert r.members() is None
    assert r.age_seconds() is None


def test_none_without_secret(auth):
    r = make(secret_getter=lambda: "")
    assert r.access("member") is None
    assert auth.calls == []


def test_signed_call_to_auth_internal_roster(auth):
    r = make()
    assert r.refresh() is True
    base, path, headers = auth.calls[0]
    assert base == "http://auth.test:8011" and path == "/api/internal/people"
    assert verify_internal(SECRET, "GET", "/api/internal/people", headers)
    assert TS_HEADER in headers and SIG_HEADER in headers


def test_browser_path_auth_url_falls_back_to_loopback(auth, monkeypatch):
    monkeypatch.delenv("STONEPI_AUTH_URL", raising=False)
    make(auth_url="/auth").refresh()
    assert auth.calls[0][0] == "http://127.0.0.1:8011"


def test_capability_defaults_applied(auth):
    r = make()
    member = r.access("member")
    assert member.has_app and member.enabled and not member.is_admin
    assert member.capabilities["can_manage_sources"] is False
    assert member.capabilities["can_use_social"] is True
    # Missing keys take the catalog default.
    assert member.capabilities["can_share_agenda"] is True
    defaults = r.access("defaults")
    assert defaults.capabilities == {
        "can_manage_sources": True,
        "can_use_social": False,
        "can_share_agenda": True,
        "can_sync_calendar": True,
    }
    assert defaults.can("can_manage_sources") and not defaults.can("can_use_social")
    assert not defaults.can("not_a_capability")


def test_no_app_access(auth):
    access = make().access("no-app")
    assert access.enabled and not access.has_app
    assert not any(access.capabilities.values())
    assert not access.can("can_manage_sources")


def test_disabled_or_deleted_user_has_nothing(auth):
    access = make().access("gone")
    assert isinstance(access, MemberAccess)
    assert access.enabled is False and access.has_app is False and access.is_admin is False
    assert access.capabilities and not any(access.capabilities.values())
    assert make().access("").has_app is False


def test_admin_has_everything_even_without_grant(auth):
    access = make().access("admin")
    assert access.is_admin and access.has_app
    assert access.capabilities and all(access.capabilities.values())
    assert access.can("can_use_social") and access.can("anything")


def test_ttl_cache(auth, clock):
    r = make(ttl=300)
    assert r.access("member") is not None
    assert r.access("member") is not None
    assert len(auth.calls) == 1
    clock.now += 299
    assert r.refresh() is False
    assert len(auth.calls) == 1
    clock.now += 2
    assert r.refresh() is True
    assert len(auth.calls) == 2
    assert r.refresh(force=True) is True
    assert len(auth.calls) == 3


def test_revoke_seen_after_ttl(auth, clock):
    r = make(ttl=300)
    assert r.access("member").can("can_use_social")
    auth.body = {"people": [{**PEOPLE[0], "permissions": {APP: {"can_use_social": False}}}]}
    assert r.access("member").can("can_use_social")  # still cached
    clock.now += 301
    assert not r.access("member").can("can_use_social")


def test_auth_down_keeps_last_good(auth, clock):
    r = make(ttl=300, max_stale=3600)
    assert r.access("member").has_app
    auth.down = True
    clock.now += 301
    assert r.refresh() is False
    assert r.access("member").can("can_use_social")  # last good roster
    # Retries back off instead of hammering Auth on every call.
    calls = len(auth.calls)
    r.access("member")
    assert len(auth.calls) == calls
    clock.now += 31
    r.access("member")
    assert len(auth.calls) == calls + 1
    # Too old: unknown again.
    clock.now += 3600
    assert r.access("member") is None


def test_bad_rosters_are_failures_and_keep_last_good(auth):
    r = make()
    assert r.refresh() is True
    for body in ({"people": []}, {"people": [{"id": "x", "is_admin": False}]}, {"nope": 1}, {"people": "x"}):
        auth.body = body
        assert r.refresh(force=True) is False
        assert r.access("member").can("can_use_social")


def test_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(internal, "get_internal_json", boom)
    r = make()
    assert r.refresh() is False
    assert r.access("member") is None
    r2 = make(secret_getter=boom)
    assert r2.refresh() is False and r2.access("x") is None


def test_members_and_access_many(auth):
    r = make()
    members = r.members()
    assert set(members) == {"member", "defaults", "no-app", "admin"}
    assert members["admin"].is_admin
    many = r.access_many(["member", "gone", None])
    assert set(many) == {"member", "gone"}
    assert many["gone"].has_app is False


def test_member_access_pure_helper():
    assert member_access(None, APP, user_id="x").has_app is False
    extra = member_access({"id": "y", "permissions": {APP: {"new_cap": True}}}, APP)
    assert extra.can("new_cap")  # Auth newer than this package's catalog


def test_get_roster_is_a_process_wide_singleton():
    reset_rosters()
    try:
        a = get_roster("pricewatch")
        assert get_roster("pricewatch") is a
        assert get_roster("newscast") is not a
    finally:
        reset_rosters()
