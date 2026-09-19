"""Shared GitHub Releases updater for StonePi monorepo apps.

Expects release assets named ``stonepi-{app_id}-{version}.zip``
(e.g. ``stonepi-newscast-0.0.2.zip``). Legacy ``{app_id}-{version}.zip``
is still accepted for one release cycle. Each zip is rooted like an app
overlay (``app/``, ``requirements.txt``, …). Platform packs use
``stonepi-platform-{version}.zip``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Callable

import httpx

logger = logging.getLogger("stonepi.update")

GITHUB_API = "https://api.github.com"
VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

DEFAULT_CODE_NAMES = (
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

LEGACY_UNITS = {
    "newscast": "newscast",
    "fileserve": "fileserve",
    "eventtrakr": "eventtrakr",
}


def normalize_repo(value: str) -> str:
    raw = (value or "").strip()
    raw = raw.removeprefix("https://github.com/").removeprefix("http://github.com/")
    raw = raw.strip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def parse_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip()
    if text.lower().startswith("v") and text[1:2].isdigit():
        text = text[1:]
    if text.lower().startswith("stonepi-v") and text[9:10].isdigit():
        text = text[9:]
    elif text.lower().startswith("stonepi-") and text[8:9].isdigit():
        text = text[8:]
    bits = []
    for part in text.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "" and bits:
            break
        bits.append(int(digits or 0))
    return tuple(bits) if bits else (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def versions_match(left: str, right: str) -> bool:
    return parse_version(left) == parse_version(right)


def extract_packaged_version(text: str) -> str:
    match = VERSION_RE.search(text or "")
    return match.group(1) if match else ""


def parse_asset_name(name: str, app_id: str) -> str | None:
    """Return version from a release asset name, or None if it is not for app_id.

    Preferred: ``stonepi-{app_id}-{version}.zip``
    Legacy: ``{app_id}-{version}.zip``
    """
    raw = (name or "").strip()
    app = re.escape(app_id)
    for pattern in (
        re.compile(rf"^stonepi-{app}-(?P<ver>\d+(?:\.\d+)*)\.zip$", re.I),
        re.compile(rf"^{app}-(?P<ver>\d+(?:\.\d+)*)\.zip$", re.I),
    ):
        match = pattern.match(raw)
        if match:
            return match.group("ver")
    return None


def find_asset(assets: list[dict], app_id: str) -> dict | None:
    best: dict | None = None
    best_ver = ""
    best_preferred = False
    for asset in assets or []:
        name = str(asset.get("name") or "")
        ver = parse_asset_name(name, app_id)
        if not ver:
            continue
        preferred = name.lower().startswith("stonepi-")
        if best is None:
            best = {**asset, "parsed_version": ver}
            best_ver = ver
            best_preferred = preferred
            continue
        newer = is_newer(ver, best_ver)
        same = versions_match(ver, best_ver)
        if newer or (same and preferred and not best_preferred):
            best = {**asset, "parsed_version": ver}
            best_ver = ver
            best_preferred = preferred
    return best


def read_platform_version(stonepi_root: Path | None = None) -> str:
    candidates: list[Path] = []
    if stonepi_root:
        candidates.append(Path(stonepi_root) / "VERSION")
    candidates.append(Path("/opt/stonepi/VERSION"))
    here = Path(__file__).resolve()
    # packages/stonepi_update/stonepi_update/core.py → repo root
    candidates.append(here.parents[3] / "VERSION")
    for path in candidates:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                if text:
                    return text
        except OSError:
            continue
    return "0.0.0"


def read_app_version(root_dir: Path) -> str:
    init_py = Path(root_dir) / "app" / "__init__.py"
    if not init_py.exists():
        return "0.0.0"
    try:
        return extract_packaged_version(init_py.read_text(encoding="utf-8", errors="replace")) or "0.0.0"
    except OSError:
        return "0.0.0"


def refresh_check(payload: dict, current: str = "") -> dict:
    data = dict(payload or {})
    running = current or str(data.get("current") or "")
    candidate = str(data.get("version") or data.get("tag") or "")
    data["current"] = running
    data["newer"] = bool(candidate) and is_newer(candidate, running)
    if data["newer"]:
        return data
    data["valid"] = False
    if candidate and versions_match(candidate, running):
        data["message"] = f"You have the latest version ({running})."
    return data


def find_release_root(extracted: Path) -> Path | None:
    def looks_like_app(path: Path) -> bool:
        if not (path / "app" / "__init__.py").exists():
            return False
        return (path / "app" / "main.py").exists() or (path / "app" / "serve.py").exists()

    if looks_like_app(extracted):
        return extracted
    for child in extracted.iterdir():
        if child.is_dir() and looks_like_app(child):
            return child
    return None


def validate_zip(zip_path: Path, *, expected_version: str = "", current_version: str = "") -> dict:
    if not zip_path.exists():
        raise ValueError("Update zip is missing.")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        root = ""
        for name in names:
            if name.endswith("app/__init__.py"):
                root = name[: -len("app/__init__.py")]
                break
        if root is None:
            raise ValueError("Zip does not contain app/__init__.py.")
        required = [f"{root}app/__init__.py", f"{root}requirements.txt"]
        has_entry = any(
            item in names for item in (f"{root}app/main.py", f"{root}app/serve.py")
        )
        missing = [item for item in required if item not in names]
        if missing or not has_entry:
            raise ValueError("Zip is missing required app files.")
        packaged = extract_packaged_version(
            archive.read(f"{root}app/__init__.py").decode("utf-8", errors="replace")
        )
        if not packaged:
            raise ValueError("Could not read the version inside the update.")
        if expected_version and not versions_match(expected_version, packaged):
            raise ValueError(
                f"Asset version {expected_version} does not match packaged version {packaged}."
            )
        if current_version and not is_newer(packaged, current_version):
            raise ValueError("This release is not newer than the running version.")
    return {"ok": True, "version": packaged, "tag": expected_version or packaged}


class Updater:
    """Stateful updater bound to one app install tree."""

    def __init__(
        self,
        *,
        app_id: str,
        root_dir: Path,
        updates_dir: Path,
        current_version: str,
        user_agent: str | None = None,
        code_names: tuple[str, ...] = DEFAULT_CODE_NAMES,
        service_names: tuple[str, ...] | None = None,
        backup_fn: Callable[[], Path | None] | None = None,
        extra_pip: list[str] | None = None,
        get_repo: Callable[[], str] | None = None,
        get_last_check: Callable[[], dict] | None = None,
        save_last_check: Callable[[dict], dict] | None = None,
        require_app_layout: bool = True,
    ) -> None:
        self.app_id = app_id
        self.root_dir = Path(root_dir)
        self.updates_dir = Path(updates_dir)
        self.current_version = current_version
        self.code_names = code_names
        self.backup_fn = backup_fn
        self.extra_pip = list(extra_pip or [])
        self.require_app_layout = require_app_layout
        self._get_repo = get_repo or (lambda: "")
        self._get_last = get_last_check or (lambda: {})
        self._save_last = save_last_check or (lambda payload: payload)
        self.headers = {
            "User-Agent": user_agent or f"StonePi-{app_id}-updater",
            "Accept": "application/vnd.github+json",
        }
        if service_names is None:
            names = [f"stonepi-{app_id}"]
            legacy = LEGACY_UNITS.get(app_id)
            if legacy:
                names.append(legacy)
            self.service_names = tuple(names)
        else:
            self.service_names = service_names

    def repo(self) -> str:
        return normalize_repo(self._get_repo())

    def last_check(self) -> dict:
        return refresh_check(self._get_last(), self.current_version)

    def _save(self, payload: dict) -> dict:
        payload = {**payload, "checked_at": datetime.now(timezone.utc).isoformat()}
        saved = self._save_last(payload)
        return refresh_check(saved if isinstance(saved, dict) else payload, self.current_version)

    def check_latest(self, *, download: bool = True) -> dict:
        repo = self.repo()
        if not repo:
            return self._save(
                {
                    "ok": False,
                    "newer": False,
                    "message": "Set the GitHub repository (owner/stonePi) before checking for updates.",
                }
            )
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=self.headers) as client:
                response = client.get(url)
                if response.status_code == 404:
                    raise ValueError("No GitHub release found for that repository.")
                response.raise_for_status()
                body = response.json()
        except ValueError as exc:
            return self._save({"ok": False, "newer": False, "repo": repo, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.info("github check failed: %s", exc)
            return self._save({"ok": False, "newer": False, "repo": repo, "message": "Could not reach GitHub."})

        release_tag = str(body.get("tag_name") or "")
        notes = (body.get("body") or "").strip()
        asset = find_asset(body.get("assets") or [], self.app_id)
        payload: dict = {
            "ok": True,
            "repo": repo,
            "release_tag": release_tag,
            "tag": "",
            "version": "",
            "name": body.get("name") or release_tag,
            "notes": notes,
            "html_url": body.get("html_url") or "",
            "zip_url": "",
            "asset_name": "",
            "newer": False,
            "current": self.current_version,
            "message": "",
        }
        if not asset:
            payload.update(
                {
                    "ok": False,
                    "message": f"Latest release has no stonepi-{self.app_id}-*.zip asset. Publish StonePi per-app packages.",
                }
            )
            return self._save(payload)

        version = str(asset.get("parsed_version") or "")
        zip_url = str(asset.get("browser_download_url") or "")
        payload.update(
            {
                "tag": version,
                "version": version,
                "zip_url": zip_url,
                "asset_name": str(asset.get("name") or ""),
                "newer": is_newer(version, self.current_version) if version else False,
            }
        )
        if not version:
            payload.update({"ok": False, "message": "Could not parse the update asset version."})
            return self._save(payload)
        if not payload["newer"]:
            payload["message"] = f"{self.app_id} is up to date ({self.current_version})."
            return self._save(payload)
        if download and zip_url:
            try:
                zip_path = self._download_zip(zip_url, version)
                if self.require_app_layout:
                    validate_zip(zip_path, expected_version=version, current_version=self.current_version)
                payload["zip_path"] = str(zip_path)
                payload["valid"] = True
                payload["message"] = f"Version {version} is ready to install."
            except Exception as exc:  # noqa: BLE001
                payload.update({"ok": False, "valid": False, "message": str(exc)})
        else:
            payload["message"] = f"Version {version} is available."
        return self._save(payload)

    def _download_zip(self, zip_url: str, version: str) -> Path:
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        staging = self.updates_dir / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        dest = staging / f"{self.app_id}-{version}.zip"
        with httpx.Client(
            timeout=httpx.Timeout(120.0, connect=10.0),
            follow_redirects=True,
            headers=self.headers,
        ) as client:
            response = client.get(zip_url)
            response.raise_for_status()
            dest.write_bytes(response.content)
        return dest

    def snapshot_current_code(self) -> Path:
        previous = self.updates_dir / "previous"
        if previous.exists():
            shutil.rmtree(previous)
        previous.mkdir(parents=True)
        for name in self.code_names:
            source = self.root_dir / name
            if not source.exists():
                continue
            target = previous / name
            if source.is_dir():
                shutil.copytree(
                    source,
                    target,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(source, target)
        return previous

    def _replace_file(self, source: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.stonepi-tmp")
        try:
            if tmp.exists():
                tmp.unlink()
            shutil.copy2(source, tmp)
            os.replace(tmp, dest)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            shutil.copy2(source, dest)

    def _remove_path(self, path: Path) -> None:
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("could not remove %s: %s", path, exc)

    def _overlay_dir(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        wanted: set[str] = set()
        for item in source.iterdir():
            if item.name == "__pycache__" or item.suffix == ".pyc":
                continue
            wanted.add(item.name)
            dest = target / item.name
            if item.is_dir():
                self._overlay_dir(item, dest)
            else:
                self._replace_file(item, dest)
        for item in list(target.iterdir()):
            if item.name in wanted or item.name == "__pycache__" or item.suffix == ".pyc":
                continue
            self._remove_path(item)

    def _copy_code_tree(self, source_root: Path, dest_root: Path) -> None:
        for name in self.code_names:
            source = source_root / name
            if not source.exists():
                continue
            target = dest_root / name
            if source.is_dir():
                self._overlay_dir(source, target)
            else:
                self._replace_file(source, target)

    def apply_zip(self, zip_path: Path, expected_version: str = "") -> dict:
        info = validate_zip(
            zip_path,
            expected_version=expected_version,
            current_version=self.current_version,
        )
        extract_to = self.updates_dir / "extracted"
        if extract_to.exists():
            shutil.rmtree(extract_to)
        extract_to.mkdir(parents=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_to)
        root = find_release_root(extract_to)
        if root is None:
            raise ValueError(f"Could not find the {self.app_id} app folder in the zip.")
        self.snapshot_current_code()
        if self.backup_fn is not None:
            try:
                self.backup_fn()
            except Exception as exc:  # noqa: BLE001
                logger.warning("pre-update backup failed: %s", exc)
        self._copy_code_tree(root, self.root_dir)
        self._install_requirements()
        return {"ok": True, "version": info["version"], "message": f"Installed {info['version']}. Restarting…"}

    def rollback_code(self) -> None:
        previous = self.updates_dir / "previous"
        if not previous.exists() or not (
            (previous / "app" / "main.py").exists() or (previous / "app" / "serve.py").exists()
        ):
            raise ValueError("No previous app snapshot to roll back to.")
        self._copy_code_tree(previous, self.root_dir)
        self._install_requirements()

    def _install_requirements(self) -> None:
        python = Path(sys.executable)
        requirements = self.root_dir / "requirements.txt"
        if requirements.exists():
            subprocess.run(
                [str(python), "-m", "pip", "install", "-r", str(requirements)],
                check=False,
                cwd=str(self.root_dir),
            )
        for extra in self.extra_pip:
            subprocess.run(
                [str(python), "-m", "pip", "install", "-e", extra],
                check=False,
                cwd=str(self.root_dir),
            )

    def install_latest(self) -> dict:
        check = self.last_check()
        zip_path = Path(check.get("zip_path") or "")
        version = str(check.get("version") or check.get("tag") or "")
        if not zip_path.exists():
            check = self.check_latest(download=True)
            zip_path = Path(check.get("zip_path") or "")
            version = str(check.get("version") or check.get("tag") or "")
        if not check.get("newer"):
            raise ValueError(check.get("message") or "No newer release to install.")
        if not zip_path.exists():
            raise ValueError(check.get("message") or "Download the update before installing.")
        return self.apply_zip(zip_path, version)

    def schedule_restart(self) -> None:
        Thread(target=self._restart_soon, daemon=True, name=f"{self.app_id}-restart").start()

    def _restart_soon(self) -> None:
        import time

        time.sleep(1.2)
        for name in self.service_names:
            unit = Path(f"/etc/systemd/system/{name}.service")
            if unit.exists():
                subprocess.Popen(["sudo", "systemctl", "restart", name], close_fds=True)
                return
        bat = self.root_dir / "run-local.bat"
        if os.name == "nt" and bat.exists():
            subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(self.root_dir), close_fds=True)
            return
        os._exit(0)


def file_store(path: Path) -> tuple[Callable[[], dict], Callable[[dict], dict], Callable[[], str], Callable[[str], None]]:
    """Simple JSON + repo helpers for apps without a settings DB (dashboard)."""

    path = Path(path)

    def load() -> dict:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_all(data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_last() -> dict:
        raw = load().get("update_last_check")
        return raw if isinstance(raw, dict) else {}

    def save_last(payload: dict) -> dict:
        data = load()
        data["update_last_check"] = payload
        save_all(data)
        return payload

    def get_repo() -> str:
        return str(load().get("github_repo") or "")

    def set_repo(value: str) -> None:
        data = load()
        data["github_repo"] = normalize_repo(value)
        save_all(data)

    return get_last, save_last, get_repo, set_repo
