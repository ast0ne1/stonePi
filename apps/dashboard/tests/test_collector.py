"""Tests for dashboard background health collector."""

from __future__ import annotations

from app import collector


def test_refresh_now_marks_ready(monkeypatch):
    monkeypatch.setattr(
        "app.services.application_cards",
        lambda cookies=None: [
            {
                "id": "auth",
                "name": "Auth",
                "enabled": True,
                "unit": "stonepi-auth",
                "unit_status": "local",
                "health": {"ok": True, "status": 200},
                "port": 8011,
                "path": "/auth",
            }
        ],
    )
    monkeypatch.setattr("app.services.backup_info", lambda: {"status": "ok", "timestamp": "2026-09-01T00:00:00+00:00"})
    monkeypatch.setattr("app.display._read_mem_pct", lambda: 42)
    monkeypatch.setattr("app.display._read_temp_c", lambda: 45.0)
    monkeypatch.setattr("app.display._read_uptime", lambda: "1d")
    monkeypatch.setattr("app.display._read_cpu_pct", lambda: 10)
    monkeypatch.setattr(
        "app.system_health.destinations_status",
        lambda: {"ntfy": {"enabled": False, "configured": False, "token_set": False}, "trmnl": {"enabled": False, "configured": False, "webhook_set": False}},
    )
    monkeypatch.setattr("app.system_health.listening_ports", lambda: {"ok": True, "expected": [], "unexpected": []})
    monkeypatch.setattr("app.system_health.hardware_status", lambda: {"ok": True})
    monkeypatch.setattr("app.system_health.ntp_status", lambda: {"synced": True})
    monkeypatch.setattr("app.system_health.recent_journal_errors", lambda lines=24: {"lines": []})
    monkeypatch.setattr(
        "app.network.network_snapshot",
        lambda: {"internet": {"ok": True}, "tailscale": {"connected": False}, "appliance": False},
    )
    monkeypatch.setattr("app.system_health.enrich_watch", lambda watch, **_: watch)
    monkeypatch.setattr("app.system_health.maybe_emit_disk_warning", lambda _watch: None)

    snap = collector.refresh_now()
    assert snap["ready"] is True
    assert snap["mem_pct"] == 42
    assert snap["cards"]
    assert snap["watch"].get("level")
    assert snap["system_health"]["ready"] is True
    assert snap["system_health"]["memory_pct"] == 42


def test_get_snapshot_is_copy():
    a = collector.get_snapshot()
    a["cards"] = [{"id": "mutated"}]
    b = collector.get_snapshot()
    assert b.get("cards") != [{"id": "mutated"}]
