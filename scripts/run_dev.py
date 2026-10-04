#!/usr/bin/env python3
"""Start StonePi apps on Windows without Nginx. SSO cookie is shared on 127.0.0.1."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SECRET_FILE = DATA / "session.secret"
AUTH_PKG = ROOT / "packages" / "stonepi_auth"
PLATFORM_PKGS = [
    ROOT / "packages" / "stonepi_auth",
    ROOT / "packages" / "stonepi_update",
    ROOT / "packages" / "stonepi_contracts",
    ROOT / "packages" / "stonepi_display",
    ROOT / "packages" / "stonepi_notify",
    ROOT / "packages" / "stonepi_watch",
    ROOT / "packages" / "stonepi_vault",
    ROOT / "packages" / "stonepi_automations",
    ROOT / "packages" / "stonepi_browser",
]

APPS = [
    ("auth", ROOT / "apps" / "auth", 8011, "", "python -m app.serve"),
    ("dashboard", ROOT / "apps" / "dashboard", 8010, "", "python -m app.serve"),
    ("notify", ROOT / "apps" / "notify", 8012, "/notify", "python -m app.serve"),
    ("newscast", ROOT / "apps" / "newscast", 8001, "", "python -m app.serve"),
    ("fileserve", ROOT / "apps" / "fileserve", 8002, "", "python run.py"),
    ("eventtrakr", ROOT / "apps" / "eventtrakr", 8003, "", "python -m app.serve"),
    ("pinboard", ROOT / "apps" / "pinboard", 8004, "", "python -m app.serve"),
    ("studio", ROOT / "apps" / "studio", 8005, "", "python -m app.serve"),
    ("pricescout", ROOT / "apps" / "pricescout", 8006, "", "python -m app.serve"),
    ("sportguide", ROOT / "apps" / "sportguide", 8007, "", "python -m app.serve"),
    ("pricewatch", ROOT / "apps" / "pricewatch", 8008, "", "python -m app.serve"),
    # No Kiwix on Windows: Library shows "Kiwix not installed" / mock mode in dev.
    ("library", ROOT / "apps" / "library", 8009, "", "python -m app.serve"),
    # Car Thing panel. Preview it in Notify → Displays → Car Thing; with adb on PATH and a
    # Car Thing plugged into this PC, the connector takes over its screen too.
    ("carthing", ROOT / "apps" / "carthing", 8013, "", "python -m app.serve"),
]


def free_dev_ports() -> None:
    """Stop leftover listeners on StonePi run-dev ports (common after a crash)."""
    ports = sorted({port for _, _, port, _, _ in APPS})
    if os.name == "nt":
        port_list = ", ".join(str(p) for p in ports)
        # A listening socket is inherited by child processes (SportGuide's Playwright driver /
        # Chromium), so the reported owner may already be dead while an orphan still holds the
        # port. Kill the owner's tree plus any orphans it left, then wait for the port to clear.
        script = f"""
$ports = @({port_list})
function Stop-Tree($procId) {{
  Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue |
    ForEach-Object {{ Stop-Tree $_.ProcessId }}
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}}
$listeners = Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue
foreach ($c in $listeners) {{
  Stop-Tree $c.OwningProcess
  Write-Output "Freed port $($c.LocalPort) (PID $($c.OwningProcess))"
}}
if ($listeners) {{
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline -and (Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue)) {{
    Start-Sleep -Milliseconds 250
  }}
  Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {{ Write-Output "WARNING: port $($_.LocalPort) still in use (PID $($_.OwningProcess))" }}
}}
"""
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line:
                print(line)
        return
    for port in ports:
        try:
            out = subprocess.check_output(["lsof", "-ti", f"tcp:{port}"], text=True).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for pid in out.split():
            try:
                os.kill(int(pid), 15)
                print(f"Freed port {port} (PID {pid})")
            except OSError:
                pass


def ensure_secret() -> str:
    DATA.mkdir(parents=True, exist_ok=True)
    exposure = DATA / "exposure"
    if not exposure.exists():
        exposure.write_text("lan\n", encoding="utf-8")
    if SECRET_FILE.exists() and SECRET_FILE.read_text(encoding="utf-8").strip():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    SECRET_FILE.write_text(value, encoding="utf-8")
    return value


def venv_python(app_dir: Path) -> Path:
    if os.name == "nt":
        return app_dir / ".venv" / "Scripts" / "python.exe"
    return app_dir / ".venv" / "bin" / "python"


def _deps_stamp(app_dir: Path) -> Path:
    return app_dir / ".venv" / ".stonepi-deps"


def _deps_fresh(app_dir: Path, req: Path) -> bool:
    stamp = _deps_stamp(app_dir)
    if not stamp.exists() or not venv_python(app_dir).exists():
        return False
    try:
        stamped = float(stamp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    newest = req.stat().st_mtime
    for pkg in PLATFORM_PKGS:
        pyproject = pkg / "pyproject.toml"
        if pyproject.exists():
            newest = max(newest, pyproject.stat().st_mtime)
    return stamped >= newest


def _mark_deps(app_dir: Path, req: Path) -> None:
    newest = req.stat().st_mtime
    for pkg in PLATFORM_PKGS:
        pyproject = pkg / "pyproject.toml"
        if pyproject.exists():
            newest = max(newest, pyproject.stat().st_mtime)
    stamp = _deps_stamp(app_dir)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(newest), encoding="utf-8")


def _pip(py: Path, *args: str) -> None:
    cmd = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(detail or f"pip failed ({result.returncode}): {' '.join(args)}")


def ensure_venv(name: str, app_dir: Path) -> Path:
    py = venv_python(app_dir)
    req = app_dir / "requirements.txt"
    if not py.exists():
        print(f"{name}: creating venv")
        subprocess.check_call(
            [sys.executable, "-m", "venv", str(app_dir / ".venv")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        py = venv_python(app_dir)
    if _deps_fresh(app_dir, req):
        return py
    print(f"{name}: installing dependencies")
    _pip(py, "--upgrade", "pip")
    _pip(py, "-r", str(req))
    _pip(py, "-e", str(AUTH_PKG))
    update_pkg = ROOT / "packages" / "stonepi_update"
    if update_pkg.exists():
        _pip(py, "-e", str(update_pkg))
    vault = ROOT / "packages" / "stonepi_vault"
    if vault.exists() and name in {
        "auth",
        "dashboard",
        "notify",
        "newscast",
        "fileserve",
        "eventtrakr",
        "pinboard",
        "studio",
        "pricescout",
        "sportguide",
        "pricewatch",
        "library",
        "carthing",
    }:
        _pip(py, "-e", str(vault))
    contracts = ROOT / "packages" / "stonepi_contracts"
    if contracts.exists() and name in {
        "notify",
        "dashboard",
        "newscast",
        "fileserve",
        "eventtrakr",
        "pinboard",
        "sportguide",
        "pricewatch",
        "library",
        "carthing",
    }:
        _pip(py, "-e", str(contracts))
    browser_pkg = ROOT / "packages" / "stonepi_browser"
    if browser_pkg.exists() and name in {"eventtrakr", "sportguide"}:
        _pip(py, "-e", str(browser_pkg))
    if name == "dashboard":
        for pkg in PLATFORM_PKGS:
            if pkg.exists() and pkg.name not in {
                "stonepi_auth",
                "stonepi_vault",
                "stonepi_contracts",
                "stonepi_notify",
                "stonepi_browser",
            }:
                _pip(py, "-e", str(pkg))
    if name == "carthing":
        _pip(py, "-e", str(ROOT / "packages" / "stonepi_display"))
    if name == "notify":
        for pkg_name in ("stonepi_display", "stonepi_notify", "stonepi_watch"):
            pkg = ROOT / "packages" / pkg_name
            if pkg.exists():
                _pip(py, "-e", str(pkg))
    _mark_deps(app_dir, req)
    return py


def main() -> None:
    free_dev_ports()
    secret = ensure_secret()
    procs: list[subprocess.Popen] = []
    try:
        time.sleep(0.4)
        for name, path, port, prefix, command in APPS:
            py = ensure_venv(name, path)
            env = os.environ.copy()
            env.update(
                {
                    "HOST": "127.0.0.1",
                    "PORT": str(port),
                    "SESSION_SECRET": secret,
                    "STONEPI_SESSION_SECRET": secret,
                    "STONEPI_VAULT_DIR": str(DATA / "vault"),
                    "STONEPI_EXPOSURE": "lan",
                    "STONEPI_EXPOSURE_FILE": str(DATA / "exposure"),
                    "STONEPI_AUTH_URL": "http://127.0.0.1:8011",
                    "STONEPI_PREFIX": prefix,
                    "STONEPI_PUBLIC_ORIGIN": "http://127.0.0.1:8010",
                    "STONEPI_HOSTNAME": "stonepi",
                    "AUTH_URL": "http://127.0.0.1:8011",
                    "PUBLIC_ORIGIN": "http://127.0.0.1:8010",
                    "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
                    "STONEPI_NOTIFY_URL": "http://127.0.0.1:8012",
                }
            )
            args = command.split()
            args[0] = str(py)
            procs.append(subprocess.Popen(args, cwd=str(path), env=env))
        print()
        print("StonePi")
        print("  Dashboard   http://127.0.0.1:8010/")
        print("  Auth        http://127.0.0.1:8011/login")
        print("  Notify      http://127.0.0.1:8012/")
        print("  NewsCast    http://127.0.0.1:8001/")
        print("  FileServe   http://127.0.0.1:8002/")
        print("  EventTrakr  http://127.0.0.1:8003/")
        print("  Pinboard    http://127.0.0.1:8004/")
        print("  Studio      http://127.0.0.1:8005/")
        print("  PriceScout  http://127.0.0.1:8006/")
        print("  SportGuide  http://127.0.0.1:8007/")
        print("  PriceWatch  http://127.0.0.1:8008/")
        print("  Library     http://127.0.0.1:8009/")
        print("  Car Thing   http://127.0.0.1:8012/notify/displays/carthing (preview)")
        print("  Login       admin / admin")
        print("Ctrl+C to stop.")
        while True:
            time.sleep(1)
            for name, proc in zip((a[0] for a in APPS), procs, strict=True):
                if proc.poll() is not None:
                    raise SystemExit(f"{name} exited with code {proc.returncode}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in procs:
            if proc.poll() is None:
                if os.name == "nt":
                    # terminate() would leave Playwright/Chromium children holding the port.
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        capture_output=True,
                    )
                else:
                    proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
