"""Settings → Updates lists the platform plus every catalog app (no hand-typed list)."""

from __future__ import annotations

from stonepi_auth import APP_IDS, UPDATABLE_APP_IDS

from app import update_service


def test_targets_are_platform_then_catalog():
    ids = [target["id"] for target in update_service.update_targets()]
    assert ids == ["platform", *APP_IDS]


def test_only_platform_shipped_apps_are_not_updatable():
    updatable = {t["id"] for t in update_service.update_targets() if t["updatable"]}
    assert updatable == update_service.updatable_ids() == {"platform", *UPDATABLE_APP_IDS}
    # System apps take the platform version and update with the platform zip.
    assert not {"dashboard", "auth", "notify", "recover"} & (updatable - {"platform"})


def test_version_card_groups_split_system_and_apps(monkeypatch):
    monkeypatch.setattr(update_service, "last_check", lambda app_id: {})
    groups = dict(update_service.version_card_groups())
    system = [card["id"] for card in groups["SYSTEM"]]
    assert system[0] == "platform"
    assert {"dashboard", "auth", "notify", "recover"} <= set(system)
    assert {"pricescout", "sportguide", "pricewatch"} <= {card["id"] for card in groups["USER"]}
    for cards in groups.values():
        for card in cards:
            assert card["version"], card["id"]


def test_platform_install_overlays_platform_shipped_apps():
    for app_id in ("dashboard", "auth", "notify", "recover"):
        assert f"apps/{app_id}" in update_service.PLATFORM_APP_PATHS


# -- on the Pi (root update helper present) ------------------------------------


def test_pi_app_updates_use_the_helper(monkeypatch, tmp_path):
    helper = tmp_path / "stonepi-update-helper"
    monkeypatch.setattr(update_service, "privileged_helper", lambda: helper)
    assert update_service._updater("pinboard").privileged_helper == helper
    assert update_service._updater("platform").privileged_helper is None


def test_pi_platform_install_points_at_the_installer(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(update_service, "privileged_helper", lambda: tmp_path / "helper")
    with pytest.raises(ValueError, match="--reinstall"):
        update_service.install_latest("platform")


def test_helper_restarts_so_dashboard_does_not(monkeypatch):
    restarts = []

    class FakeUpdater:
        def install_latest(self):
            return {"ok": True, "version": "0.0.7", "restart": False, "message": "Updated to 0.0.7."}

        def schedule_restart(self):
            restarts.append(True)

    monkeypatch.setattr(update_service, "_updater", lambda app_id: FakeUpdater())
    assert update_service.install_latest("pinboard")["message"] == "Updated to 0.0.7."
    assert restarts == []


def test_failed_helper_install_does_not_restart(monkeypatch):
    restarts = []

    class FakeUpdater:
        def install_latest(self):
            return {"ok": False, "restart": False, "message": "pinboard did not pass its health check. Put back 0.0.6."}

        def schedule_restart(self):
            restarts.append(True)

    monkeypatch.setattr(update_service, "_updater", lambda app_id: FakeUpdater())
    assert update_service.install_latest("pinboard")["ok"] is False
    assert restarts == []
