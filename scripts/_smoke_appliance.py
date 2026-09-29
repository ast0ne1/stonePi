"""Smoke checks for appliance hardening (local, no Pi)."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "packages" / "stonepi_watch"),
    str(ROOT / "packages" / "stonepi_contracts"),
    str(ROOT / "packages" / "stonepi_auth"),
]

import stonepi_watch
from stonepi_watch.status import evaluate
import stonepi_watch.status as st

assert (
    stonepi_watch.DISK_ATTENTION_PCT,
    stonepi_watch.DISK_SERIOUS_PCT,
    stonepi_watch.DISK_CRITICAL_PCT,
) == (75, 85, 95)

orig_disk = st._disk_pct
try:
    for pct, expect in [(74, "healthy"), (75, "attention"), (85, "attention"), (95, "critical")]:
        st._disk_pct = lambda data_dir=None, p=pct: p
        result = evaluate(
            health_url_for=lambda item: "http://127.0.0.1:9/none",
            backup_info=lambda: {"status": "ok", "timestamp": "2099-01-01T00:00:00+00:00"},
            disabled_ids=set(),
            catalog=[],
        )
        assert result["level"] == expect, (pct, result["level"], result)
        print(f"disk {pct}% -> {result['level']} OK")
finally:
    st._disk_pct = orig_disk

td = tempfile.mkdtemp()
path = Path(td) / "emit-retry.jsonl"
os.environ["STONEPI_EMIT_RETRY_PATH"] = str(path)
os.environ["STONEPI_NOTIFY_URL"] = "http://127.0.0.1:1"
import stonepi_contracts.emit as emit_mod

importlib.reload(emit_mod)
ok = emit_mod.emit_event({"id": "system.disk_warning", "source": "system", "title": "t"}, timeout=0.2)
assert ok is False, f"expected emit failure, got {ok}"
assert emit_mod.RETRY_QUEUE.is_file(), "queue should exist after failed emit"
print("emit queue lines:", len(emit_mod.RETRY_QUEUE.read_text().splitlines()))

# Import dashboard system_health as a package
dash_root = ROOT / "apps" / "dashboard"
sys.path.insert(0, str(dash_root))
os.chdir(dash_root)
import app.system_health as sh  # noqa: E402

listen = sh.listening_ports()
assert "listeners" in listen
ntp = sh.ntp_status()
assert "synchronized" in ntp
hw = sh.hardware_status()
assert "undervoltage" in hw
journal = sh.recent_journal_errors()
assert "entries" in journal
dest = sh.destinations_status()
assert "ntfy" in dest and "trmnl" in dest
print("system_health helpers OK", "listen_appliance=", listen.get("appliance"))

enriched = sh.enrich_watch(
    {"level": "healthy", "reasons": [], "summary": "All clear"},
    listening={"appliance": True, "ok": False, "unexpected": [{"proto": "tcp", "port": 1234}]},
    hardware={"undervoltage": False, "throttling": False},
)
assert enriched["level"] == "attention"
enriched2 = sh.enrich_watch(
    {"level": "healthy", "reasons": [], "summary": "All clear"},
    listening={"appliance": True, "ok": True, "unexpected": []},
    hardware={"undervoltage": True, "throttling": False},
)
assert enriched2["level"] == "critical"
print("enrich_watch OK")

rec_root = ROOT / "apps" / "recover"
sys.path.insert(0, str(rec_root))
# Avoid clobbering dashboard `app` package
if "app" in sys.modules:
    del sys.modules["app"]
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
from app.main import app as recover_app  # noqa: E402

assert recover_app.title == "StonePi Recover"
print("recover app OK")
print("ALL SMOKE OK")
