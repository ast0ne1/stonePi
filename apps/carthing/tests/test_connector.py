"""ADB connector: inject once per device, keep the firmware, restore on disable."""

from __future__ import annotations

import pytest
from stonepi_display import carthing

from app import connector as connector_mod
from app import state


class FakeAdb:
    def __init__(self) -> None:
        self.devices = {"CT123": True, "PHONE9": False}  # serial -> is a Car Thing
        self.marker = ""
        self.calls: list[str] = []
        self.pushed = ""
        self.backlight = "/sys/class/backlight/aml-bl"

    def __call__(self, args: list[str], timeout: float = 20.0):
        line = " ".join(args)
        self.calls.append(line)
        if args == ["devices"]:
            rows = "".join(f"{s}\tdevice\n" for s in self.devices)
            return 0, f"List of devices attached\n{rows}"
        serial = args[1] if args[:1] == ["-s"] else ""
        rest = args[2:]
        if rest[:1] == ["shell"]:
            cmd = rest[1]
            if "qt-superbird-app/webapp/ ] && echo exists" in cmd:
                return 0, "exists\n" if self.devices.get(serial) else ""
            if cmd.startswith("cat /usr/share/qt-superbird-app/webapp/stonepi-panel.txt"):
                return 0, self.marker
            if cmd.startswith("ls -d /sys/class/backlight"):
                return 0, self.backlight + "/\n"
            if cmd.endswith("max_brightness"):
                return 0, "255\n"
            return 0, ""
        if rest[:1] == ["push"]:
            from pathlib import Path

            self.pushed = (Path(rest[1]) / "index.html").read_text(encoding="utf-8")
            self.marker = (Path(rest[1]) / "stonepi-panel.txt").read_text(encoding="utf-8")
            return 0, "1 file pushed"
        if rest[:1] == ["reverse"]:
            return 0, "tcp:8013 tcp:8013" if rest[1:2] == ["--list"] else ""
        return 0, ""


@pytest.fixture
def setup(monkeypatch):
    fake = FakeAdb()
    panel = {"value": {"enabled": True, "token_hash": ""}}
    issued: list[str] = []

    def _pair():
        token = f"tok{len(issued)}"
        issued.append(token)
        panel["value"]["token_hash"] = carthing.hash_token(token)
        return token

    monkeypatch.setattr(state, "panel_state", lambda draft=False: panel["value"])
    monkeypatch.setattr(state, "pair_device", _pair)
    conn = connector_mod.Connector()
    conn.adb = "adb"
    monkeypatch.setattr(conn, "_run", fake)
    return conn, fake, panel, issued


def test_injects_once_with_token_and_skips_other_devices(setup):
    conn, fake, panel, issued = setup
    conn.poll()
    assert issued == ["tok0"]
    assert "http://localhost:8013/?t=tok0" in fake.pushed
    assert "PHONE9" not in conn.devices and "PHONE9" in conn.not_carthing
    joined = "\n".join(fake.calls)
    assert "-s CT123 reverse tcp:8013 tcp:8013" in joined
    assert "mount --bind /tmp/stonepi-panel /usr/share/qt-superbird-app/webapp" in joined
    assert "rm -rf /usr/share" not in joined and "remount,rw" not in joined  # firmware untouched
    assert conn.has_backlight()
    calls_before = len(fake.calls)
    conn.poll()  # already injected: no new token, no push
    assert issued == ["tok0"]
    assert not any("push" in c for c in fake.calls[calls_before:])


def test_service_restart_reuses_existing_page(setup):
    conn, fake, panel, issued = setup
    conn.poll()
    fresh = connector_mod.Connector()
    fresh.adb = "adb"
    fresh._run = fake  # type: ignore[method-assign]
    fresh.poll()
    assert issued == ["tok0"]  # marker matched the current token: nothing re-pushed
    assert fresh.devices["CT123"].injected


def test_disable_restores_firmware_app(setup):
    conn, fake, panel, _ = setup
    conn.poll()
    panel["value"]["enabled"] = False
    conn.poll()
    joined = "\n".join(fake.calls)
    assert "umount /usr/share/qt-superbird-app/webapp" in joined
    assert "supervisorctl restart chromium" in joined
    assert not conn.devices["CT123"].injected


def test_backlight_levels(setup):
    conn, fake, _, _ = setup
    conn.poll()
    assert conn.set_level(50)
    assert any("echo 128 > /sys/class/backlight/aml-bl/brightness" in c for c in fake.calls)
    count = len(fake.calls)
    assert conn.set_level(50)  # unchanged level: no adb call
    assert len(fake.calls) == count
    assert conn.set_level(0)
    assert any("echo 0 > /sys/class/backlight/aml-bl/brightness" in c for c in fake.calls)


def test_unplug_forgets_device(setup):
    conn, fake, _, _ = setup
    conn.poll()
    fake.devices = {}
    conn.poll()
    assert conn.devices == {}
