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
