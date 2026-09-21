"""Unit tests for Tailscale status parsing (no live Tailscale required)."""

from __future__ import annotations

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


def test_running_with_magicdns():
    raw = {
        "BackendState": "Running",
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
    assert status["access_url"] == "https://stonepi.tailnet-name.ts.net/"


def test_wanted_off_is_disabled_even_if_running():
    raw = {
        "BackendState": "Running",
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
