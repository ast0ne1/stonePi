"""Notify's TRMNL collector must emit the shared merge_variables contract."""

from __future__ import annotations

from stonepi_display import MERGE_VARIABLE_KEYS

from app import collect


def test_collect_overview_emits_contract_keys(monkeypatch):
    monkeypatch.setattr(collect, "_fetch", lambda url, client: {"feeds": 3} if "8001" in url else {})
    monkeypatch.setattr(
        collect,
        "_watch",
        lambda: {"level": "healthy", "summary": "All clear", "apps": [{"id": "newscast", "n": "NewsCast", "running": True}]},
    )
    monkeypatch.setattr(collect, "_backup", lambda: {"status": "ok", "timestamp": "2026-01-01T00:00:00+00:00"})

    variables = collect.collect_overview()

    assert set(variables) == MERGE_VARIABLE_KEYS
    assert variables["nc_feeds"] == 3
    assert variables["system_ok"] is True
    assert variables["watch_summary"] == "All clear"
    assert variables["apps"][0]["n"] == "NewsCast"
    assert variables["backup_ok"] is True
