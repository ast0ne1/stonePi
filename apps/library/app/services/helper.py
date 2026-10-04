"""Calls into the root helper (/usr/local/sbin/stonepi-library-helper).

The Library app never runs as root: installing Kiwix, mounting a drive, the
Kiwix unit and the backup setting all go through this one sudo entry. Off the
Pi (run-dev on Windows, or LIBRARY_DEV_HELPER=1) the helper is simulated so every
page still works. On a Pi a missing helper is an error, never a fake success.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, env

logger = logging.getLogger("library.helper")
DEV_STATE = DATA_DIR / "dev-helper.json"
MISSING_HELPER = "Library helper not installed — re-run the installer"


def simulated() -> bool:
    """Dev only: Windows run-dev or an explicit LIBRARY_DEV_HELPER=1."""
    return os.name != "posix" or (os.environ.get("LIBRARY_DEV_HELPER") or "").strip() == "1"


def available() -> bool:
    return not simulated() and Path(env.helper).exists()


def run(*args: str, timeout: float = 120) -> tuple[bool, dict[str, Any]]:
    """Run a helper action; returns (ok, parsed JSON from its last output line)."""
    if simulated():
        return _simulate(list(args))
    if not Path(env.helper).exists():
        logger.warning("helper %s: %s not found", args[:1], env.helper)
        return False, {"error": MISSING_HELPER}
    try:
        proc = subprocess.run(
            ["sudo", "-n", env.helper, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("helper %s failed: %s", args[:1], exc)
        return False, {"error": str(exc)}
    payload: dict[str, Any] = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                pass
            break
    if proc.returncode != 0:
        payload.setdefault("error", (proc.stderr or proc.stdout).strip()[-400:] or f"exit {proc.returncode}")
        logger.warning("helper %s exit %s: %s", args[:1], proc.returncode, payload.get("error"))
        return False, payload
    return True, payload


# ---------- simulation (Windows / dev) ----------

def _dev_state() -> dict[str, Any]:
    try:
        return json.loads(DEV_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"kiwix_installed": False, "kiwix_active": False, "include_content": False}


def _save_dev(state: dict[str, Any]) -> None:
    DEV_STATE.parent.mkdir(parents=True, exist_ok=True)
    DEV_STATE.write_text(json.dumps(state), encoding="utf-8")


def _simulate(args: list[str]) -> tuple[bool, dict[str, Any]]:
    state = _dev_state()
    action = args[0] if args else ""
    if action == "status":
        return True, {
            "kiwix_installed": state["kiwix_installed"],
            "kiwix_version": "3.x (simulated)" if state["kiwix_installed"] else "",
            "kiwix_active": state["kiwix_active"],
            "include_content": state.get("include_content", False),
            "simulated": True,
        }
    if action == "install-kiwix":
        state["kiwix_installed"] = True
    elif action == "remove-kiwix":
        state.update(kiwix_installed=False, kiwix_active=False)
    elif action == "kiwix" and len(args) > 1:
        state["kiwix_active"] = args[1] in {"start", "restart"} and state["kiwix_installed"]
    elif action == "backup-content" and len(args) > 1:
        state["include_content"] = args[1] == "on"
    elif action in {"mount-drive", "unmount-drive"}:
        return False, {"error": "Drives can only be mounted on the Pi"}
    elif action in {"set-storage", "prepare-folder"}:
        pass
    else:
        return False, {"error": f"unknown action {action}"}
    _save_dev(state)
    return True, {"ok": True, "simulated": True}
