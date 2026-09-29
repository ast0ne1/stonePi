"""Per-account Instagram schedule due-checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.social.poll import _account_due


def test_social_account_due_follows_global_interval():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    local = now.astimezone()
    account = SimpleNamespace(
        tracking_enabled=True,
        schedule_mode="global",
        schedule_config="",
        interval_minutes=None,
        last_checked=now - timedelta(minutes=10),
    )
    assert _account_due(account, 30, now, local) is False
    account.last_checked = now - timedelta(minutes=31)
    assert _account_due(account, 30, now, local) is True


def test_social_account_due_custom_interval():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    local = now.astimezone()
    account = SimpleNamespace(
        tracking_enabled=True,
        schedule_mode="custom",
        schedule_config='{"mode": "interval", "interval_minutes": 120}',
        interval_minutes=120,
        last_checked=now - timedelta(minutes=60),
    )
    assert _account_due(account, 30, now, local) is False
    account.last_checked = now - timedelta(minutes=121)
    assert _account_due(account, 30, now, local) is True


def test_social_account_paused_never_due():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    account = SimpleNamespace(
        tracking_enabled=False,
        schedule_mode="global",
        schedule_config="",
        interval_minutes=None,
        last_checked=None,
    )
    assert _account_due(account, 30, now, now.astimezone()) is False
