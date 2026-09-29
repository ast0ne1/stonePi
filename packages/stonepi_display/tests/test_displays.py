from __future__ import annotations

import json
from pathlib import Path

import stonepi_display
from stonepi_display.displays import BUILTIN_DASHBOARD_ID, load_displays, save_displays


def test_displays_builtin(tmp_path: Path):
    stonepi_display.configure_displays(tmp_path)
    displays = load_displays()
    assert any(d["id"] == BUILTIN_DASHBOARD_ID and d["builtin"] for d in displays)
    assert (tmp_path / "displays.json").exists()


def test_migrate_legacy_layout(tmp_path: Path):
    legacy = {
        "enabled": True,
        "webhook_url": "https://example.com/hook",
        "interval_minutes": 30,
        "device": "og",
        "design": "custom",
        "layout": {"cols": 2, "items": [{"id": "system", "span": 2}, {"id": "apps", "span": 1}]},
    }
    (tmp_path / "display.json").write_text(json.dumps(legacy), encoding="utf-8")
    stonepi_display.configure_displays(tmp_path)
    displays = load_displays()
    dash = next(d for d in displays if d["id"] == BUILTIN_DASHBOARD_ID)
    ids = {w["widget_id"] for w in dash["widgets"]}
    assert "stonepi.system_status" in ids
    assert "stonepi.apps" in ids
    assert (tmp_path / "migration_trmnl.json").exists()


def test_unreadable_dashboard_legacy_does_not_crash(tmp_path: Path, monkeypatch):
    """Notify runs as stonepi-notify and cannot stat Dashboard data/."""
    stonepi_display.configure_displays(tmp_path)
    real_is_file = Path.is_file

    def guarded(self: Path) -> bool:
        if "dashboard" in self.as_posix():
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", guarded)
    displays = load_displays()
    assert any(d["id"] == BUILTIN_DASHBOARD_ID for d in displays)


def test_cannot_normalize_unknown_widget(tmp_path: Path):
    stonepi_display.configure_displays(tmp_path)
    save_displays(
        [
            {
                "id": BUILTIN_DASHBOARD_ID,
                "name": "Dashboard",
                "builtin": True,
                "enabled": True,
                "widgets": [{"id": "x", "widget_id": "nope.missing", "size": "medium"}],
            }
        ]
    )
    dash = load_displays()[0]
    assert dash["widgets"] == []
