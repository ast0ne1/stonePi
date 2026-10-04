"""Shared SportGuide test setup: no live Auth roster calls, fresh roster per test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def offline_roster(monkeypatch):
    """Auth's roster is unknown (None) unless a test serves one; never hits the network."""
    from stonepi_auth import internal, roster

    roster.reset_rosters()
    monkeypatch.setattr(internal, "get_internal_json", lambda *a, **kw: None)
    yield
    roster.reset_rosters()
