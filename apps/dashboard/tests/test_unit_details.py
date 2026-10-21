"""Service details for the Car Thing's service page: systemctl show (no privileges) plus the
last log lines through the service helper; a signed endpoint for catalog units only."""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal

from app import routes, services
from app.main import app

SECRET = "dash-test-secret"
PATH = routes.DETAILS_PATH
SHOW = """ActiveState=active
SubState=running
ActiveEnterTimestamp=@1761040000
InactiveEnterTimestamp=@1761030000
NRestarts=2
MemoryCurrent=92274688
Result=success
"""
LOGS = """Oct 21 11:07:59 stonepi python3[812]: Fetching 4 feeds
Oct 21 11:08:01 stonepi python3[812]: Fetched 4 feeds in 1.9 s
-- Boot 1234 --
Oct 21 11:08:02 stonepi systemd[1]: stonepi-newscast.service: Consumed 3.1s CPU time.
"""


def _fake_pi(monkeypatch, show=SHOW):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        out = show if cmd[0] == "systemctl" else LOGS
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(services.os, "name", "posix")
    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services.subprocess, "run", run)
    return calls


def test_details_read_systemd_and_the_last_log_lines(monkeypatch):
    calls = _fake_pi(monkeypatch)
    d = services.unit_details("stonepi-newscast", log_lines=5)
    assert d["ok"] and d["state"] == "active" and d["sub"] == "running" and d["since"] == 1761040000
    assert d["memory_bytes"] == 92274688 and d["restarts"] == 2 and d["result"] == "success"
    assert d["logs"] == [
        {"time": "11:07", "text": "Fetching 4 feeds"},
        {"time": "11:08", "text": "Fetched 4 feeds in 1.9 s"},
        {"time": "11:08", "text": "stonepi-newscast.service: Consumed 3.1s CPU time."},
    ]
    assert calls[0][:3] == ["systemctl", "show", "--timestamp=unix"] and calls[0][-1] == "stonepi-newscast"
    assert calls[1] == ["sudo", "-n", services.SERVICE_HELPER, "logs", "stonepi-newscast", "5"]


def test_details_stopped_service_says_since_when_it_stopped(monkeypatch):
    _fake_pi(monkeypatch, show=SHOW.replace("ActiveState=active", "ActiveState=failed").replace("MemoryCurrent=92274688", "MemoryCurrent=[not set]"))
    d = services.unit_details("stonepi-newscast")
    assert d["state"] == "failed" and d["since"] == 1761030000 and d["memory_bytes"] is None


def test_details_refuse_other_units(monkeypatch):
    calls = _fake_pi(monkeypatch)
    assert services.unit_details("ssh")["ok"] is False and calls == []


def test_endpoint_signed_and_catalog_only(monkeypatch):
    monkeypatch.setattr(services, "session_secret", lambda: SECRET)
    monkeypatch.setattr(services, "unit_details", lambda unit: {"ok": True, "unit": unit, "state": "active"})
    client = TestClient(app)
    assert client.post(PATH, json={"unit": "stonepi-newscast"}).status_code == 403
    body = client.post(PATH, json={"unit": "stonepi-newscast"}, headers=sign_internal(SECRET, "POST", PATH)).json()
    assert body == {"ok": True, "unit": "stonepi-newscast", "state": "active"}
    for unit in ("ssh", "stonepi-carthing; reboot", ""):
        assert client.post(PATH, json={"unit": unit}, headers=sign_internal(SECRET, "POST", PATH)).status_code == 400
