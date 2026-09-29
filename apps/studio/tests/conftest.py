"""Keep every test off the real Studio data (apps/studio/data or /var/lib/stonepi/studio)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    from app import store

    monkeypatch.setattr(store, "STORE", tmp_path / "studio.json")
    monkeypatch.setattr(store, "WORKSPACE_DIR", tmp_path / "ws")
