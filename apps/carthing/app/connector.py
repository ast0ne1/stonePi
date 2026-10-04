"""Connect a Car Thing over ADB without touching its firmware.

Method adapted from pything's ``device-carthing`` app (https://github.com/trwy7/pything, MIT):

1. A serial is a Car Thing when ``/usr/share/qt-superbird-app/webapp/`` exists on it.
2. ``adb reverse tcp:8013 tcp:8013`` — the device's localhost:8013 is the Pi's panel.
3. A tiny redirect page is pushed to ``/tmp/stonepi-panel`` and bind-mounted over the web app
   folder, then the firmware's Chromium is restarted. ``/tmp`` and the mount vanish when the
   device reboots, so the firmware's own web app comes back untouched.
4. Switching the panel off (or stopping the service) unmounts and restarts Chromium.

Unlike pything we never replace the web app permanently, never download ADB, and never prompt.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.config import ROOT_DIR, env

logger = logging.getLogger("carthing.connector")

WEBAPP = "/usr/share/qt-superbird-app/webapp"
DEVICE_DIR = "/tmp/stonepi-panel"
MARKER = "stonepi-panel.txt"
POLL_S = 3.0
REVERSE_CHECK_S = 30.0
CTROOT_TEMPLATE = ROOT_DIR / "app" / "templates" / "ctroot" / "index.html"


class Device:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.injected = False
        self.backlight: str | None = None  # sysfs folder, e.g. /sys/class/backlight/aml-bl
        self.max_brightness = 255
        self.level: int | None = None
        self.reverse_checked = 0.0


class Connector:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.not_carthing: set[str] = set()
        self.adb = shutil.which(env.adb_path) if env.adb_enabled else None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    # ── adb plumbing ────────────────────────────────────────────────────────

    def _run(self, args: list[str], timeout: float = 20.0) -> tuple[int, str]:
        if not self.adb:
            return 1, "adb not available"
        try:
            proc = subprocess.run(
                [self.adb, *args], capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, str(exc)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def _shell(self, serial: str, command: str, timeout: float = 20.0) -> tuple[int, str]:
        return self._run(["-s", serial, "shell", command], timeout=timeout)

    def _serials(self) -> list[str]:
        rc, out = self._run(["devices"], timeout=10)
        if rc != 0:
            return []
        serials = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def _is_carthing(self, serial: str) -> bool:
        _, out = self._shell(serial, f"[ -d {WEBAPP}/ ] && echo exists", timeout=10)
        return out.strip().endswith("exists")

    # ── lifecycle ───────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return bool(self.adb)

    def start(self) -> None:
        if not self.adb:
            logger.info("Car Thing connector off (adb %s)", "disabled" if not env.adb_enabled else "not found")
            return
        self._thread = threading.Thread(target=self._loop, name="carthing-connector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.restore_all()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - keep polling whatever one pass hits
                logger.exception("connector pass failed")
            self._stop.wait(POLL_S)

    def poll(self) -> None:
        from app import state

        panel = state.panel_state() or {}
        enabled = bool(panel.get("enabled"))
        connected = self._serials()
        with self._lock:
            for serial in list(self.devices):
                if serial not in connected:
                    logger.info("Car Thing %s disconnected", serial)
                    del self.devices[serial]
            self.not_carthing &= set(connected)
            for serial in connected:
                if serial in self.not_carthing:
                    continue
                device = self.devices.get(serial)
                if device is None:
                    if not self._is_carthing(serial):
                        self.not_carthing.add(serial)
                        continue
                    logger.info("Car Thing %s connected", serial)
                    device = self.devices[serial] = Device(serial)
                if not enabled:
                    if device.injected:
                        self.restore(device)
                    continue
                self._ensure(device, str(panel.get("token_hash") or ""))

    def _ensure(self, device: Device, token_hash: str) -> None:
        now = time.monotonic()
        if not device.injected:
            self._inject(device, token_hash)
            return
        if now - device.reverse_checked > REVERSE_CHECK_S:
            device.reverse_checked = now
            _, out = self._run(["-s", device.serial, "reverse", "--list"], timeout=10)
            if f"tcp:{env.port}" not in out:
                self._run(["-s", device.serial, "reverse", f"tcp:{env.port}", f"tcp:{env.port}"])

    def _inject(self, device: Device, token_hash: str) -> None:
        from app import state

        serial = device.serial
        rc, out = self._run(["-s", serial, "reverse", f"tcp:{env.port}", f"tcp:{env.port}"])
        if rc != 0:
            self.last_error = f"adb reverse failed: {out.strip()[:160]}"
            logger.warning("%s: %s", serial, self.last_error)
            return
        device.reverse_checked = time.monotonic()
        _, marker = self._shell(serial, f"cat {WEBAPP}/{MARKER} 2>/dev/null", timeout=10)
        if token_hash and marker.strip() == token_hash[:16]:
            # Already showing our page with the current token (e.g. the service restarted).
            device.injected = True
            self._find_backlight(device)
            return
        token = state.pair_device()
        if not token:
            self.last_error = "Notify didn't issue a device token"
            logger.warning("%s: %s", serial, self.last_error)
            return
        from stonepi_display.carthing import hash_token

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ctroot"
            root.mkdir()
            page = CTROOT_TEMPLATE.read_text(encoding="utf-8")
            page = page.replace("__PORT__", str(env.port)).replace("__TOKEN__", token)
            (root / "index.html").write_text(page, encoding="utf-8")
            (root / MARKER).write_text(hash_token(token)[:16], encoding="utf-8")
            self._shell(serial, "supervisorctl stop chromium")
            self._shell(serial, f"mountpoint -q {WEBAPP} && umount {WEBAPP}")
            self._shell(serial, f"rm -rf {DEVICE_DIR}")
            rc, out = self._run(["-s", serial, "push", str(root), DEVICE_DIR], timeout=60)
            if rc != 0:
                self.last_error = f"adb push failed: {out.strip()[:160]}"
                self._shell(serial, "supervisorctl start chromium")
                return
            rc, out = self._shell(serial, f"mount --bind {DEVICE_DIR} {WEBAPP}")
            self._shell(serial, "supervisorctl start chromium")
        if rc != 0:
            self.last_error = f"mount failed: {out.strip()[:160]}"
            return
        device.injected = True
        device.level = None
        self.last_error = ""
        self._find_backlight(device)
        logger.info("Car Thing %s now shows the StonePi panel", serial)

    def restore(self, device: Device) -> None:
        """Give the device its own web app back (unmount + restart Chromium)."""
        self._shell(device.serial, f"mountpoint -q {WEBAPP} && umount {WEBAPP}")
        self._shell(device.serial, "supervisorctl restart chromium")
        self._run(["-s", device.serial, "reverse", "--remove", f"tcp:{env.port}"], timeout=10)
        device.injected = False
        logger.info("Car Thing %s restored to its own web app", device.serial)

    def restore_all(self) -> None:
        with self._lock:
            for device in list(self.devices.values()):
                if device.injected:
                    self.restore(device)

    # ── backlight ───────────────────────────────────────────────────────────

    def _find_backlight(self, device: Device) -> None:
        _, out = self._shell(device.serial, "ls -d /sys/class/backlight/*/ 2>/dev/null | head -n1", timeout=10)
        folder = out.strip().rstrip("/")
        if not folder.startswith("/sys/class/backlight/"):
            device.backlight = None
            return
        _, raw = self._shell(device.serial, f"cat {folder}/max_brightness", timeout=10)
        try:
            device.max_brightness = max(1, int(raw.strip()))
        except ValueError:
            device.max_brightness = 255
        device.backlight = folder

    def has_backlight(self) -> bool:
        with self._lock:
            return any(d.injected and d.backlight for d in self.devices.values())

    def set_level(self, percent: int) -> bool:
        """Backlight to ``percent`` (0 = off) on every connected panel. False if none can."""
        percent = max(0, min(100, int(percent)))
        done = False
        with self._lock:
            targets = [d for d in self.devices.values() if d.injected and d.backlight]
        for device in targets:
            if device.level == percent:
                done = True
                continue
            value = round(device.max_brightness * percent / 100)
            if percent > 0:
                value = max(1, value)
            rc, _ = self._shell(device.serial, f"echo {value} > {device.backlight}/brightness", timeout=10)
            if rc == 0:
                device.level = percent
                done = True
        return done

    def repair(self) -> None:
        """Re-pair: forget injections so the next pass pushes a page with a fresh token."""
        with self._lock:
            for device in self.devices.values():
                device.injected = False

    def status(self) -> dict:
        with self._lock:
            return {
                "adb": bool(self.adb),
                "devices": [
                    {"serial": d.serial, "injected": d.injected, "backlight": bool(d.backlight)}
                    for d in self.devices.values()
                ],
                "error": self.last_error,
            }


connector = Connector()
