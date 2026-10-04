"""Catalog capability defaults: old grants, new people, explicit saves, internal roster."""

from __future__ import annotations

import json
import os
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="auth-capdefaults-")
os.environ.setdefault("STONEPI_SESSION_SECRET", "test-secret")
os.environ.setdefault("STONEPI_DATA_DIR", _TMP)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/users.sqlite")
os.environ.setdefault("STONEPI_VAULT_DIR", os.path.join(_TMP, "vault"))

import pytest  # noqa: E402

from app import users as users_svc  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AppGrant  # noqa: E402

EVENTTRAKR_DEFAULTS = {
    "can_manage_sources": True,
    "can_use_social": False,
    "can_share_agenda": True,
    "can_sync_calendar": True,
}


@pytest.fixture(scope="module", autouse=True)
def _tables():
    init_db()


def _member(db, **kw):
    return users_svc.create_user(
        db, username=f"u{uuid.uuid4().hex[:10]}", password="password123", apps=["dashboard", "eventtrakr"], **kw
    )


def test_grant_saved_before_capabilities_existed_gets_defaults():
    db = SessionLocal()
    user = _member(db)
    grant = next(g for g in user.grants if g.app_id == "eventtrakr")
    grant.capabilities = "{}"  # what every pre-upgrade EventTrakr grant holds
    db.commit()
    db.refresh(user)
    assert users_svc.granted_permissions(user)["eventtrakr"] == EVENTTRAKR_DEFAULTS
    db.close()


def test_new_person_without_explicit_permissions_gets_defaults():
    db = SessionLocal()
    user = _member(db)
    assert users_svc.granted_permissions(user)["eventtrakr"] == EVENTTRAKR_DEFAULTS
    # Other apps without declared defaults stay off.
    assert not any(users_svc.granted_permissions(user).get("dashboard", {}).values())
    db.close()


def test_explicit_admin_choices_win_over_defaults():
    db = SessionLocal()
    choice = {"can_manage_sources": False, "can_use_social": True, "can_share_agenda": False, "can_sync_calendar": False}
    user = _member(db, permissions={"eventtrakr": choice})
    assert users_svc.granted_permissions(user)["eventtrakr"] == choice
    stored = json.loads(db.query(AppGrant).filter_by(user_id=user.id, app_id="eventtrakr").one().capabilities)
    assert stored == choice
    db.close()


def test_admin_gets_every_capability():
    db = SessionLocal()
    admin = _member(db, is_admin=True)
    assert all(users_svc.granted_permissions(admin)["eventtrakr"].values())
    db.close()


def test_internal_roster_carries_permissions():
    db = SessionLocal()
    user = _member(db)
    rows = {row["id"]: row for row in users_svc.internal_people(db)}
    assert rows[user.id]["permissions"]["eventtrakr"] == EVENTTRAKR_DEFAULTS
    db.close()
