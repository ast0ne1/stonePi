"""Unit tests for Health listening-port + hardware helpers."""

from __future__ import annotations

from app.system_health import classify_listeners, _read_boot_count, _read_cpu_freq_mhz


def test_mdns_and_tailscale_companions_are_expected():
    lines = [
        "udp UNCONN 0 0 0.0.0.0:41641 0.0.0.0:* users:((\"tailscaled\",pid=800,fd=20))",
        "udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:((\"avahi-daemon\",pid=512,fd=12))",
        "udp UNCONN 0 0 0.0.0.0:40624 0.0.0.0:*",
        "udp UNCONN 0 0 0.0.0.0:58828 0.0.0.0:*",
        "tcp LISTEN 0 128 0.0.0.0:8099 0.0.0.0:* users:((\"python\",pid=900,fd=3))",
        "tcp LISTEN 0 128 127.0.0.1:8001 0.0.0.0:*",
        "tcp LISTEN 0 128 100.64.1.2:65290 0.0.0.0:*",
        "tcp LISTEN 0 128 0.0.0.0:49028 0.0.0.0:* users:((\"tailscaled\",pid=800,fd=22))",
    ]
    result = classify_listeners(lines)
    assert result["ok"] is True
    assert result["unexpected"] == []


def test_unknown_non_loopback_tcp_is_flagged():
    lines = [
        "tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:*",
        "tcp LISTEN 0 128 0.0.0.0:31337 0.0.0.0:* users:((\"evil\",pid=1,fd=1))",
    ]
    result = classify_listeners(lines)
    assert result["ok"] is False
    assert [(r["proto"], r["port"]) for r in result["unexpected"]] == [("tcp", 31337)]


def test_avahi_proc_suffix_allowed_without_fixed_port():
    # Extra Avahi socket beyond 5353, identified by process name.
    lines = [
        "udp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*",
        "udp UNCONN 0 0 192.168.1.10:41234 0.0.0.0:* users:((\"avahi-daemon:r\",pid=512,fd=13))",
    ]
    result = classify_listeners(lines)
    assert result["ok"] is True
    assert result["unexpected"] == []


def test_boot_count_falls_back_to_last_reboot(monkeypatch):
    def fake_run(cmd, **_kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[:2] == ["journalctl", "--list-boots"]:
            return R()
        if cmd[:2] == ["last", "-x"]:
            r = R()
            r.stdout = (
                "reboot   system boot  6.12.0   Sat Sep 20 10:00   still running\n"
                "reboot   system boot  6.12.0   Fri Sep 19 08:00 - 10:00  (1+02:00)\n"
                "wtmp begins Fri Sep  1 00:00:00 2026\n"
            )
            return r
        return R()

    monkeypatch.setattr("app.system_health.subprocess.run", fake_run)
    assert _read_boot_count() == 2


def test_cpu_freq_from_sysfs(monkeypatch, tmp_path):
    freq = tmp_path / "scaling_cur_freq"
    freq.write_text("1500000\n", encoding="utf-8")

    class FakePath:
        def __init__(self, *parts):
            joined = "/".join(str(p) for p in parts)
            self._p = freq if "scaling_cur_freq" in joined else tmp_path / "nope"

        def read_text(self, encoding="utf-8"):
            return self._p.read_text(encoding=encoding)

    monkeypatch.setattr("app.system_health.Path", FakePath)
    monkeypatch.setattr(
        "app.system_health.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("vcgencmd should not run")),
    )
    assert _read_cpu_freq_mhz() == 1500
