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
    ROOT / "packages" / "stonepi_display",
    ROOT / "packages" / "stonepi_watch",
    ROOT / "packages" / "stonepi_vault",
    ROOT / "packages" / "stonepi_automations",
]

APPS = [
    ("auth", ROOT / "apps" / "auth", 8011, "", "python -m app.serve"),
    ("dashboard", ROOT / "apps" / "dashboard", 8010, "", "python -m app.serve"),
    ("newscast", ROOT / "apps" / "newscast", 8001, "", "python -m app.serve"),
    ("fileserve", ROOT / "apps" / "fileserve", 8002, "", "python run.py"),
    ("eventtrakr", ROOT / "apps" / "eventtrakr", 8003, "", "python -m app.serve"),
    ("pinboard", ROOT / "apps" / "pinboard", 8004, "", "python -m app.serve"),
    ("studio", ROOT / "apps" / "studio", 8005, "", "python -m app.serve"),
    ("pricescout", ROOT / "apps" / "pricescout", 8006, "", "python -m app.serve"),
    ("sportguide", ROOT / "apps" / "sportguide", 8007, "", "python -m app.serve"),
]


def free_dev_ports() -> None:
    """Stop leftover listeners on StonePi run-dev ports (common after a crash)."""
    ports = sorted({port for _, _, port, _, _ in APPS})
    if os.name == "nt":
        port_list = ", ".join(str(p) for p in ports)
        script = f"""
$ports = @({port_list})
Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {{
    $procId = $_.OwningProcess
    $port = $_.LocalPort
    try {{
      Stop-Process -Id $procId -Force -ErrorAction Stop
      Write-Output "Freed port $port (PID $procId)"
    }} catch {{}}
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
    if vault.exists() and name in {"auth", "dashboard", "newscast", "fileserve", "eventtrakr", "pinboard", "studio", "pricescout", "sportguide"}:
        _pip(py, "-e", str(vault))
    if name == "dashboard":
        for pkg in PLATFORM_PKGS:
            if pkg.exists() and pkg.name not in {"stonepi_auth", "stonepi_vault"}:
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
                }
            )
            args = command.split()
            args[0] = str(py)
            procs.append(subprocess.Popen(args, cwd=str(path), env=env))
        print()
        print("StonePi")
        print("  Dashboard   http://127.0.0.1:8010/")
        print("  Auth        http://127.0.0.1:8011/login")
        print("  NewsCast    http://127.0.0.1:8001/")
        print("  FileServe   http://127.0.0.1:8002/")
        print("  EventTrakr  http://127.0.0.1:8003/")
        print("  Pinboard    http://127.0.0.1:8004/")
        print("  Studio      http://127.0.0.1:8005/")
        print("  PriceScout  http://127.0.0.1:8006/")
        print("  SportGuide  http://127.0.0.1:8007/")
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
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
