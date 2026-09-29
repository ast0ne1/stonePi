"""Unit tests for Tailscale status parsing and network status TTL cache."""

from __future__ import annotations

import app.network as network
from app.network import parse_tailscale_status


def test_needs_login_with_auth_url():
    raw = {
        "BackendState": "NeedsLogin",
        "AuthURL": "https://login.tailscale.com/a/example",
        "Installed": True,
    }
    status = parse_tailscale_status(raw, wanted=True)
    assert status["installed"] is True
    assert status["wanted"] is True
    assert status["connected"] is False
    assert status["needs_login"] is True
    assert status["auth_url"] == "https://login.tailscale.com/a/example"
    assert status["state"] == "needs_login"
    assert status["serve_http"] is False
    assert status["access_url"] is None


def test_running_without_serve_uses_http_ip():
    raw = {
        "BackendState": "Running",
        "ServeHTTP": False,
        "Self": {
            "DNSName": "stonepi.tailnet-name.ts.net.",
            "TailscaleIPs": ["100.64.1.2", "fd7a:115c::1"],
        },
        "CurrentTailnet": {"MagicDNSEnabled": True},
    }
    status = parse_tailscale_status(raw, wanted=True)
    assert status["connected"] is True
    assert status["state"] == "connected"
    assert status["ipv4"] == "100.64.1.2"
    assert status["dns_name"] == "stonepi.tailnet-name.ts.net"
    assert status["magicdns"] is True
    assert status["magicdns_name"] == "stonepi.tailnet-name.ts.net"
    assert status["serve_http"] is False
    assert status["access_url"] == "http://100.64.1.2/"


def test_running_with_serve_uses_https_magicdns():
    raw = {
        "BackendState": "Running",
        "ServeHTTP": True,
        "Self": {
            "DNSName": "stonepi.tailnet-name.ts.net.",
            "TailscaleIPs": ["100.64.1.2"],
        },
        "CurrentTailnet": {"MagicDNSEnabled": True},
    }
    status = parse_tailscale_status(raw, wanted=True)
    assert status["serve_http"] is True
    assert status["access_url"] == "https://stonepi.tailnet-name.ts.net/"


def test_serve_enable_url_passthrough():
    raw = {
        "BackendState": "Running",
        "ServeHTTP": False,
        "ServeEnableURL": "https://login.tailscale.com/f/serve?node=abc",
        "Self": {
            "DNSName": "stonepi.tailnet.ts.net.",
            "TailscaleIPs": ["100.64.1.2"],
        },
        "CurrentTailnet": {"MagicDNSEnabled": True},
    }
    status = parse_tailscale_status(raw, wanted=True)
    assert status["serve_enable_url"] == "https://login.tailscale.com/f/serve?node=abc"
    assert status["access_url"] == "http://100.64.1.2/"


def test_wanted_off_is_disabled_even_if_running():
    raw = {
        "BackendState": "Running",
        "ServeHTTP": True,
        "Self": {"DNSName": "stonepi.tailnet.ts.net.", "TailscaleIPs": ["100.64.1.2"]},
        "CurrentTailnet": {"MagicDNSEnabled": True},
    }
    status = parse_tailscale_status(raw, wanted=False)
    assert status["connected"] is True
    assert status["state"] == "disabled"
    assert status["wanted"] is False


def test_not_installed():
    status = parse_tailscale_status({"Installed": False, "BackendState": "NoState"}, wanted=False)
    assert status["installed"] is False
    assert status["state"] == "not_installed"
    assert status["connected"] is False


def test_tailscale_status_cache_hit(monkeypatch):
    network.clear_network_cache()
    calls = {"n": 0}

    def fake_raw():
        calls["n"] += 1
        return {
            "BackendState": "Running",
            "ServeHTTP": False,
            "Self": {"DNSName": "pi.ts.net.", "TailscaleIPs": ["100.64.9.9"]},
            "CurrentTailnet": {"MagicDNSEnabled": True},
            "Installed": True,
        }

    monkeypatch.setattr(network, "helper_available", lambda: True)
    monkeypatch.setattr(network, "tailscale_wanted", lambda: True)
    monkeypatch.setattr(network, "_raw_status_from_helper", fake_raw)

    first = network.tailscale_status()
    second = network.tailscale_status()
    assert first["access_url"] == "http://100.64.9.9/"
    assert second["access_url"] == first["access_url"]
    assert calls["n"] == 1


def test_tailscale_status_fresh_bypasses_cache(monkeypatch):
    network.clear_network_cache()
    calls = {"n": 0}

    def fake_raw():
        calls["n"] += 1
        return {
            "BackendState": "Running",
            "ServeHTTP": False,
            "Self": {"DNSName": "pi.ts.net.", "TailscaleIPs": ["100.64.9.9"]},
            "CurrentTailnet": {"MagicDNSEnabled": True},
            "Installed": True,
        }

    monkeypatch.setattr(network, "helper_available", lambda: True)
    monkeypatch.setattr(network, "tailscale_wanted", lambda: True)
    monkeypatch.setattr(network, "_raw_status_from_helper", fake_raw)

    network.tailscale_status()
    network.tailscale_status(fresh=True)
    assert calls["n"] == 2


def test_clear_network_cache_invalidates(monkeypatch):
    network.clear_network_cache()
    calls = {"n": 0}

    def fake_raw():
        calls["n"] += 1
        return {
            "BackendState": "Running",
            "ServeHTTP": True,
            "Self": {"DNSName": "pi.ts.net.", "TailscaleIPs": ["100.64.9.9"]},
            "CurrentTailnet": {"MagicDNSEnabled": True},
            "Installed": True,
        }

    monkeypatch.setattr(network, "helper_available", lambda: True)
    monkeypatch.setattr(network, "tailscale_wanted", lambda: True)
    monkeypatch.setattr(network, "_raw_status_from_helper", fake_raw)

    network.tailscale_status()
    network.clear_network_cache()
    network.tailscale_status()
    assert calls["n"] == 2


def test_apply_hostname_rejects_invalid():
    ok, detail = network.apply_hostname("has space")
    assert ok is False
    assert "hyphen" in detail.lower() or "letters" in detail.lower()


def test_apply_hostname_file_only_without_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(network, "hostname_helper_available", lambda: False)
    host_file = tmp_path / "hostname"
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(host_file))
    ok, detail = network.apply_hostname("living-room")
    assert ok is True
    assert detail == "living-room"
    assert host_file.read_text(encoding="utf-8").strip() == "living-room"
