"""Health must still load when Notify's data folder is unreadable (fresh-install permissions)."""

from __future__ import annotations

from pathlib import Path

from app import system_health


def test_destinations_status_survives_permission_denied(monkeypatch, tmp_path):
    locked = tmp_path / "notify"
    locked.mkdir()
    monkeypatch.setattr(system_health, "NOTIFY_DATA", locked)
    real_is_file = Path.is_file

    def denied(self):
        if locked in self.parents:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", denied)
    status = system_health.destinations_status()
    assert status["ntfy"]["enabled"] is False


def test_ntfy_on_without_household_topic(monkeypatch, tmp_path):
    # Phone alerts keep topics per person, so destinations.json has no ``topic``.
    data = tmp_path / "notify"
    data.mkdir()
    (data / "destinations.json").write_text('{"ntfy": {"enabled": true, "server": "https://ntfy.sh"}}', encoding="utf-8")
    monkeypatch.setattr(system_health, "NOTIFY_DATA", data)
    assert system_health.destinations_status()["ntfy"]["configured"] is True


def test_carthing_enabled_from_notify_data(monkeypatch, tmp_path):
    data = tmp_path / "notify"
    (data / "carthing").mkdir(parents=True)
    monkeypatch.setattr(system_health, "NOTIFY_DATA", data)
    assert system_health.destinations_status()["carthing"]["enabled"] is False
    (data / "carthing" / "device.json").write_text('{"enabled": true}', encoding="utf-8")
    assert system_health.destinations_status()["carthing"]["enabled"] is True
