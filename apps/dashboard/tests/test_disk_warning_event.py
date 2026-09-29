"""system.disk_warning is an admin-audience event."""

from __future__ import annotations

from app import system_health


def test_disk_warning_is_admin_audience(monkeypatch):
    import stonepi_contracts

    sent = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: sent.append(ev) or True)
    system_health.maybe_emit_disk_warning({"disk_pct": 97})
    assert sent and sent[0]["id"] == "system.disk_warning"
    assert sent[0]["audience"] == "admin"
    assert "user" not in sent[0]


def test_no_warning_below_threshold(monkeypatch):
    import stonepi_contracts

    sent = []
    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: sent.append(ev) or True)
    system_health.maybe_emit_disk_warning({"disk_pct": 10})
    assert sent == []
