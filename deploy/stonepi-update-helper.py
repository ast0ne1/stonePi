#!/usr/bin/python3
"""Privileged helper for Dashboard → Settings → Updates (installed as /usr/local/sbin/stonepi-update-helper).

Dashboard runs as stonepi-dash and cannot write the root-owned app code under /opt/stonepi.
It downloads and checks a release, then asks this helper (one sudoers line) to install it:

  stonepi-update-helper apply APP_ID ZIP SHA256   install a downloaded app zip
  stonepi-update-helper rollback APP_ID           put back the code saved by the last apply
  stonepi-update-helper status APP_ID             installed version and whether a saved copy exists

apply: checks the app is an updatable catalog app, the zip sits in Dashboard's updates folder and
matches SHA256, copies it somewhere Dashboard cannot touch, validates it, saves the current code,
lays the new code in (root-owned), installs requirements into that app's own venv, restarts the
unit and waits for its health check. If any step after saving fails, the saved code goes back.

Prints one JSON object on stdout; exit status 0 on success. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

STONEPI_ROOT = Path("/opt/stonepi")
CATALOG_FILE = STONEPI_ROOT / "packages" / "stonepi_auth" / "stonepi_auth" / "catalog.py"
# Where Dashboard (stonepi-dash) downloads release zips; the only place apply accepts one from.
DOWNLOADS_ROOT = Path("/var/lib/stonepi/dashboard/updates")
# Root-only: saved code and private working copies. Dashboard must not be able to plant files here.
STATE_ROOT = Path("/var/lib/stonepi/updates")
CODE_NAMES = (
    "app",
    "deploy",
    "requirements.txt",
    "README.md",
    "INSTALL.md",
    "CHANGELOG.md",
    ".env.example",
    "run.py",
    "run-local.bat",
)
HEALTH_TIMEOUT = 45.0
PIP_TIMEOUT = 900
APP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")
MAX_ZIP_BYTES = 200 * 1024 * 1024


class HelperError(Exception):
    pass


# -- catalog + versions ---------------------------------------------------------


def load_catalog() -> list[dict]:
    """APP_CATALOG from the installed platform (root-owned code, plain data, no imports)."""
    namespace: dict = {}
    exec(compile(CATALOG_FILE.read_text(encoding="utf-8"), str(CATALOG_FILE), "exec"), namespace)
    return list(namespace.get("APP_CATALOG") or [])


def catalog_entry(app_id: str) -> dict:
    if not APP_ID_RE.match(app_id or ""):
        raise HelperError("Invalid app id.")
    for item in load_catalog():
        if item.get("id") == app_id:
            if item.get("ships_with"):
                raise HelperError(f"{app_id} updates with the StonePi platform, not on its own.")
            return item
    raise HelperError(f"{app_id} is not a StonePi app.")


def parse_version(value: str) -> tuple[int, ...]:
    bits = []
    for part in (value or "").strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "" and bits:
            break
        bits.append(int(digits or 0))
    return tuple(bits) if bits else (0,)


def read_version(app_dir: Path) -> str:
    init = app_dir / "app" / "__init__.py"
    try:
        match = VERSION_RE.search(init.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return match.group(1) if match else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -- zip handling -----------------------------------------------------------------


def private_copy(app_id: str, zip_arg: str, expected_sha: str, work: Path) -> Path:
    """Copy the downloaded zip out of Dashboard's reach, then check its hash (no swap after check)."""
    if not SHA_RE.match((expected_sha or "").lower()):
        raise HelperError("A SHA-256 checksum is required.")
    allowed = (DOWNLOADS_ROOT / app_id).resolve()
    source = Path(zip_arg).resolve()
    if allowed not in source.parents or source.suffix.lower() != ".zip" or not source.is_file():
        raise HelperError("The update zip must be one Dashboard downloaded.")
    if source.stat().st_size > MAX_ZIP_BYTES:
        raise HelperError("The update zip is too large.")
    dest = work / "update.zip"
    shutil.copyfile(source, dest)
    if sha256_file(dest) != expected_sha.lower():
        raise HelperError("The update zip does not match its published checksum.")
    return dest


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """Extract without absolute paths, '..' or symlinks. Returns the folder holding app/."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        for info in archive.infolist():
            name = info.filename
            parts = Path(name).parts
            if name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise HelperError(f"Unsafe path in update zip: {name}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise HelperError(f"Symlink in update zip: {name}")
        root = next((n[: -len("app/__init__.py")] for n in names if n.endswith("app/__init__.py")), None)
        if root is None:
            raise HelperError("The update zip has no app/__init__.py.")
        needed = [f"{root}app/__init__.py", f"{root}requirements.txt"]
        if any(n not in names for n in needed) or not any(
            f"{root}app/{entry}" in names for entry in ("main.py", "serve.py")
        ):
            raise HelperError("The update zip is missing required app files.")
        archive.extractall(dest)
    return dest / root if root else dest


# -- code tree ----------------------------------------------------------------------


def _ignored(name: str) -> bool:
    return name == "__pycache__" or name.endswith(".pyc")


def copy_code(source_root: Path, dest_root: Path) -> None:
    """Make dest_root's code names match source_root's: add, replace, and drop stale files."""
    for name in CODE_NAMES:
        source = source_root / name
        target = dest_root / name
        if not source.exists():
            continue
        if source.is_dir():
            _sync_dir(source, target)
        else:
            _replace(source, target)


def _sync_dir(source: Path, target: Path) -> None:
    if target.exists() and not target.is_dir():
        target.unlink()
    target.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for item in source.iterdir():
        if _ignored(item.name):
            continue
        wanted.add(item.name)
        if item.is_dir():
            _sync_dir(item, target / item.name)
        else:
            _replace(item, target / item.name)
    for item in list(target.iterdir()):
        if item.name in wanted or _ignored(item.name):
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _replace(source: Path, target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.stonepi-new")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)


def save_code(app_dir: Path, snapshot: Path) -> None:
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(parents=True)
    for name in CODE_NAMES:
        source = app_dir / name
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, snapshot / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, snapshot / name)


def set_code_ownership(app_dir: Path, data_owner: tuple[int, int] | None) -> None:
    """Code is root-owned and world-readable; app/data keeps the service user if it had it."""
    for name in CODE_NAMES:
        path = app_dir / name
        if not path.exists():
            continue
        for item in [path, *path.rglob("*")] if path.is_dir() else [path]:
            if item.is_symlink():
                continue
            _chown(item, 0, 0)
            os.chmod(item, 0o755 if item.is_dir() else 0o644)
    data = app_dir / "app" / "data"
    if data_owner and data.is_dir():
        for item in [data, *data.rglob("*")]:
            _chown(item, *data_owner)


def _chown(path: Path, uid: int, gid: int) -> None:
    if hasattr(os, "chown"):
        os.chown(path, uid, gid)


def owner_of(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_uid, info.st_gid)


# -- service ------------------------------------------------------------------------


def run(cmd: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def unit_name(app_id: str, entry: dict) -> str:
    return str(entry.get("unit") or f"stonepi-{app_id}")


def install_requirements(app_dir: Path, unit: str) -> None:
    pip = app_dir / ".venv" / "bin" / "pip"
    if not pip.exists():
        raise HelperError(f"{app_dir.name} has no virtualenv; re-run the StonePi installer.")
    result = run([str(pip), "install", "--disable-pip-version-check", "-r", str(app_dir / "requirements.txt")], timeout=PIP_TIMEOUT)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["pip failed"]
        raise HelperError(f"Installing requirements failed: {tail[0]}")
    # The venv stays root:root and read-only to the service user (as install.sh leaves it):
    # this helper runs its pip as root, so a venv the service could write would hand it root.


def restart_and_wait(unit: str, entry: dict) -> None:
    run(["systemctl", "restart", unit])
    port = int(entry.get("port") or 0)
    path = str(entry.get("health") or "/healthz")
    if not port:
        if run(["systemctl", "is-active", unit]).returncode != 0:
            raise HelperError(f"{unit} did not start.")
        return
    url = f"http://127.0.0.1:{port}{path}"
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1.5)
    raise HelperError(f"{unit} did not pass its health check within {int(HEALTH_TIMEOUT)}s.")


# -- commands -----------------------------------------------------------------------


def app_paths(app_id: str) -> tuple[Path, Path]:
    app_dir = STONEPI_ROOT / "apps" / app_id
    if not (app_dir / "app").is_dir():
        raise HelperError(f"{app_id} is not installed.")
    return app_dir, STATE_ROOT / app_id / "previous"


def cmd_apply(app_id: str, zip_arg: str, expected_sha: str) -> dict:
    entry = catalog_entry(app_id)
    app_dir, snapshot = app_paths(app_id)
    unit = unit_name(app_id, entry)
    installed = read_version(app_dir)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_ROOT, 0o700)
    with tempfile.TemporaryDirectory(dir=STATE_ROOT) as tmp:
        work = Path(tmp)
        zip_path = private_copy(app_id, zip_arg, expected_sha, work)
        release = safe_extract(zip_path, work / "extracted")
        version = read_version(release)
        if not version:
            raise HelperError("Could not read the version inside the update.")
        if installed and parse_version(version) <= parse_version(installed):
            raise HelperError(f"{version} is not newer than the installed {installed}.")
        data_owner = owner_of(app_dir / "app" / "data")
        save_code(app_dir, snapshot)
        (snapshot.parent / "previous-version").write_text(installed + "\n", encoding="utf-8")
        try:
            copy_code(release, app_dir)
            set_code_ownership(app_dir, data_owner)
            install_requirements(app_dir, unit)
            restart_and_wait(unit, entry)
        except Exception as exc:
            restored = _restore(app_dir, snapshot, unit, entry, data_owner)
            message = str(exc) if isinstance(exc, HelperError) else f"Update failed: {exc}"
            return {
                "ok": False,
                "app": app_id,
                "version": installed,
                "rolled_back": restored,
                "message": f"{message} Put back {installed}." if restored else f"{message} Could not put back {installed}; open the Recovery Console.",
            }
    return {"ok": True, "app": app_id, "version": version, "previous": installed, "message": f"Updated to {version}."}


def _restore(app_dir: Path, snapshot: Path, unit: str, entry: dict, data_owner) -> bool:
    try:
        copy_code(snapshot, app_dir)
        set_code_ownership(app_dir, data_owner)
        install_requirements(app_dir, unit)
        restart_and_wait(unit, entry)
        return True
    except Exception:
        return False


def cmd_rollback(app_id: str) -> dict:
    entry = catalog_entry(app_id)
    app_dir, snapshot = app_paths(app_id)
    if not (snapshot / "app" / "__init__.py").exists():
        raise HelperError(f"No saved copy of {app_id} to roll back to.")
    target = read_version(snapshot)
    current = read_version(app_dir)
    ok = _restore(app_dir, snapshot, unit_name(app_id, entry), entry, owner_of(app_dir / "app" / "data"))
    if not ok:
        return {"ok": False, "app": app_id, "version": read_version(app_dir), "message": "Roll back failed; open the Recovery Console."}
    return {"ok": True, "app": app_id, "version": target, "previous": current, "message": f"Rolled back to {target}."}


def cmd_status(app_id: str) -> dict:
    catalog_entry(app_id)
    app_dir, snapshot = app_paths(app_id)
    saved = read_version(snapshot) if (snapshot / "app" / "__init__.py").exists() else ""
    return {"ok": True, "app": app_id, "version": read_version(app_dir), "saved_version": saved}


def main(argv: list[str]) -> int:
    usage = "usage: stonepi-update-helper apply APP_ID ZIP SHA256 | rollback APP_ID | status APP_ID"
    try:
        if len(argv) == 4 and argv[0] == "apply":
            result = cmd_apply(argv[1], argv[2], argv[3])
        elif len(argv) == 2 and argv[0] == "rollback":
            result = cmd_rollback(argv[1])
        elif len(argv) == 2 and argv[0] == "status":
            result = cmd_status(argv[1])
        else:
            result = {"ok": False, "message": usage}
    except HelperError as exc:
        result = {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — always answer Dashboard with JSON
        result = {"ok": False, "message": f"Update helper error: {exc}"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print(json.dumps({"ok": False, "message": "Run as root (sudo)."}))
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
