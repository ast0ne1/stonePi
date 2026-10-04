"""Home tile live status is fetched in the background, never on the page render."""

from __future__ import annotations

import threading
import time

from app import services


class _SlowClient:
    def __init__(self, gate: threading.Event, payload: dict | None = None):
        self.gate = gate
        self.calls = 0
        self.payload = payload or {"state": "downloading", "detail": "Installing Wikipedia · 71%"}

    def get(self, url):
        self.calls += 1
        self.gate.wait(5)

        payload = self.payload

        class _Resp:
            status_code = 200

            def json(self):
                return payload

        return _Resp()


def test_slow_app_never_blocks_home(monkeypatch):
    gate = threading.Event()
    client = _SlowClient(gate)
    monkeypatch.setattr(services, "_TILE_HTTP", client)
    monkeypatch.setattr(services, "_TILE_STATUS", {})
    monkeypatch.setattr(services, "_TILE_REFRESHING", set())
    item = {"id": "library", "port": 8014, "live_status": True}

    start = time.monotonic()
    assert services._tile_status(item) == ""  # first render: description fallback
    assert services._tile_status(item) == ""  # still loading: no second request
    assert time.monotonic() - start < 0.5
    assert client.calls <= 1

    gate.set()
    deadline = time.monotonic() + 5
    while "library" not in services._TILE_STATUS and time.monotonic() < deadline:
        time.sleep(0.01)
    assert services._tile_status(item) == "Installing Wikipedia · 71%"
    assert client.calls == 1


def test_apps_without_live_status_are_skipped():
    assert services._tile_status({"id": "news", "port": 8001}) == ""


def test_idle_app_keeps_its_standard_description(monkeypatch):
    gate = threading.Event()
    gate.set()
    client = _SlowClient(gate, {"state": "ready", "detail": "Knots"})
    monkeypatch.setattr(services, "_TILE_HTTP", client)
    monkeypatch.setattr(services, "_TILE_STATUS", {})
    monkeypatch.setattr(services, "_TILE_REFRESHING", set())
    item = {"id": "library", "port": 8009, "live_status": True}
    services._tile_status(item)
    deadline = time.monotonic() + 5
    while "library" not in services._TILE_STATUS and time.monotonic() < deadline:
        time.sleep(0.01)
    assert services._tile_status(item) == ""  # Home falls back to the catalog description
