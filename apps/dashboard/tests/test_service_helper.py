"""Services controls go through the root stonepi-service-helper, never sudo systemctl/journalctl."""

from __future__ import annotations

import subprocess

from app import services


def _fake_pi(monkeypatch):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="log line\n", stderr="")

    monkeypatch.setattr(services.os, "name", "posix")
    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services.subprocess, "run", run)
    return calls


def test_control_unit_uses_helper(monkeypatch):
    calls = _fake_pi(monkeypatch)
    ok, _ = services.control_unit("stonepi-newscast", "restart")
    assert ok
    assert calls == [["sudo", "-n", services.SERVICE_HELPER, "restart", "stonepi-newscast"]]


def test_unit_logs_uses_helper_and_clamps_lines(monkeypatch):
    calls = _fake_pi(monkeypatch)
    assert services.unit_logs("stonepi-auth") == "log line"
    services.unit_logs("stonepi-auth", lines=999999)
    assert calls == [
        ["sudo", "-n", services.SERVICE_HELPER, "logs", "stonepi-auth", "40"],
        ["sudo", "-n", services.SERVICE_HELPER, "logs", "stonepi-auth", "2000"],
    ]


def test_rejects_non_stonepi_units(monkeypatch):
    calls = _fake_pi(monkeypatch)
    for unit in ("nginx", "stonepi-auth ssh", "stonepi-auth --now", "../stonepi-x", ""):
        ok, _ = services.control_unit(unit, "stop")
        assert not ok
        assert services.unit_logs(unit) == "Not a StonePi service."
    assert calls == []
    ok, message = services.control_unit("stonepi-auth", "enable")
    assert not ok and message == "Unsupported action"
