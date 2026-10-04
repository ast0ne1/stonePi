"""Keep tests off the real Auth roster: unknown (None) unless a test serves one."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_auth_roster(monkeypatch):
    from stonepi_auth.roster import reset_rosters

    reset_rosters()
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: None)
    yield
    reset_rosters()
