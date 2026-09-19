from __future__ import annotations

import json
from pathlib import Path

from app.config import DATA_DIR, ROOT_DIR, env
from stonepi_update import Updater, normalize_repo, read_app_version, read_platform_version, refresh_check

UPDATE_STORE = DATA_DIR / "updates-state.json"
UPDATES_DIR = DATA_DIR / "updates"

APP_TARGETS = (
    ("platform", "StonePi platform"),
    ("auth", "Authentication"),
    ("dashboard", "Dashboard"),
    ("newscast", "NewsCast"),
    ("fileserve", "FileServe"),
    ("eventtrakr", "EventTrakr"),
    ("pinboard", "Pinboard"),
    ("studio", "Studio"),
)

APP_COLORS = {
    "platform": "#0a6e6e",
    "auth": "#6d645a",
    "dashboard": "#b08900",
    "newscast": "#8b1e1e",
    "fileserve": "#1d5a8a",
    "eventtrakr": "#3a5628",
    "pinboard": "#6b4c2a",
    "studio": "#5c3d6e",
}


def stonepi_root() -> Path:
    local = ROOT_DIR.parent.parent
    if (local / "apps").is_dir() or (local / "VERSION").exists():
        return local
    opt = Path("/opt/stonepi")
    if opt.exists():
        return opt
    return local


def _load_store() -> dict:
    if not UPDATE_STORE.exists():
        return {}
    try:
        data = json.loads(UPDATE_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_store(data: dict) -> None:
    UPDATE_STORE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def github_repo() -> str:
    stored = str(_load_store().get("github_repo") or "")
    return normalize_repo(stored or getattr(env, "github_repo", "") or "")


def set_github_repo(value: str) -> str:
    repo = normalize_repo(value)
    data = _load_store()
    data["github_repo"] = repo
    _save_store(data)
    return repo


def platform_version() -> str:
    return read_platform_version(stonepi_root())


def app_root(app_id: str) -> Path:
    if app_id == "platform":
        return stonepi_root()
    return stonepi_root() / "apps" / app_id


def current_version(app_id: str) -> str:
    if app_id == "platform":
        return platform_version()
    return read_app_version(app_root(app_id))


def _extra_pip() -> list[str]:
    root = stonepi_root()
    extras = []
    for name in ("stonepi_auth", "stonepi_update"):
        path = root / "packages" / name
        if path.exists():
            extras.append(str(path))
    return extras


def _asset_id(app_id: str) -> str:
    return "stonepi-platform" if app_id == "platform" else app_id


def _updater(app_id: str) -> Updater:
    root = app_root(app_id)
    updates = UPDATES_DIR / app_id
    if app_id == "platform":
        code_names = ("VERSION", "deploy", "packages", "scripts", "README.md", "CHANGELOG.md")
        service_names: tuple[str, ...] = ()
        require_app = False
        extra: list[str] = []
    else:
        code_names = (
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
        service_names = (f"stonepi-{app_id}",)
        require_app = True
        extra = _extra_pip()

    def get_last() -> dict:
        return ((_load_store().get("checks") or {}).get(app_id) or {})

    def save_last(payload: dict) -> dict:
        data = _load_store()
        checks = dict(data.get("checks") or {})
        checks[app_id] = payload
        data["checks"] = checks
        if "github_repo" not in data:
            data["github_repo"] = github_repo()
        _save_store(data)
        return payload

    return Updater(
        app_id=_asset_id(app_id),
        root_dir=root,
        updates_dir=updates,
        current_version=current_version(app_id),
        user_agent="StonePi-dashboard-updater",
        code_names=code_names,
        service_names=service_names,
        extra_pip=extra,
        get_repo=github_repo,
        get_last_check=get_last,
        save_last_check=save_last,
        require_app_layout=require_app,
    )


def last_check(app_id: str) -> dict:
    payload = ((_load_store().get("checks") or {}).get(app_id) or {})
    return refresh_check(payload, current_version(app_id))


def check_latest(app_id: str, download: bool = True) -> dict:
    return _updater(app_id).check_latest(download=download)


def install_latest(app_id: str) -> dict:
    if app_id == "platform":
        return _install_platform()
    updater = _updater(app_id)
    result = updater.install_latest()
    updater.schedule_restart()
    return result


def _install_platform() -> dict:
    import shutil
    import zipfile

    updater = _updater("platform")
    check = updater.last_check()
    zip_path = Path(check.get("zip_path") or "")
    version = str(check.get("version") or check.get("tag") or "")
    if not zip_path.exists():
        check = updater.check_latest(download=True)
        zip_path = Path(check.get("zip_path") or "")
        version = str(check.get("version") or check.get("tag") or "")
    if not check.get("newer"):
        raise ValueError(check.get("message") or "No newer platform release.")
    if not zip_path.exists():
        raise ValueError(check.get("message") or "Download the platform update first.")

    extract_to = updater.updates_dir / "extracted"
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_to.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_to)

    root = extract_to
    for child in extract_to.iterdir():
        if child.is_dir() and ((child / "deploy").exists() or (child / "VERSION").exists()):
            root = child
            break
    updater.snapshot_current_code()
    updater._copy_code_tree(root, updater.root_dir)
    return {"ok": True, "version": version, "message": f"Installed platform {version}."}


def version_cards() -> list[dict]:
    from stonepi_auth import app_by_id

    cards = []
    for app_id, label in APP_TARGETS:
        catalog = app_by_id(app_id) or {}
        cards.append(
            {
                "id": app_id,
                "name": label,
                "color": catalog.get("color") or APP_COLORS.get(app_id, ""),
                "version": current_version(app_id),
                "check": last_check(app_id),
                "root": str(app_root(app_id)),
            }
        )
    return cards
