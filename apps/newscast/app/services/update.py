from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app import __version__
from app.config import ROOT_DIR, UPDATES_DIR, env
from app.services import backup, settings
from stonepi_update import Updater, normalize_repo, refresh_check

_AUTH_PKG = ROOT_DIR.parent.parent / "packages" / "stonepi_auth"
_UPDATE_PKG = ROOT_DIR.parent.parent / "packages" / "stonepi_update"

__all__ = [
    "normalize_repo",
    "refresh_check",
    "repo_from_db",
    "last_check",
    "check_latest",
    "install_latest",
    "apply_zip",
    "rollback_code",
    "schedule_restart",
]


def _extra_pip() -> list[str]:
    extras = []
    for path in (_AUTH_PKG, _UPDATE_PKG):
        if path.exists():
            extras.append(str(path))
    return extras


def _updater(db: Session | None = None) -> Updater:
    def get_repo() -> str:
        if env.stonepi_session_secret.strip():
            return env.github_repo
        if db is None:
            return env.github_repo
        return settings.get_value(db, "github_repo") or env.github_repo

    def get_last() -> dict:
        if db is None:
            return {}
        raw = settings.get_value(db, "update_last_check")
        if not raw:
            return {}
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def save_last(payload: dict) -> dict:
        if db is None:
            return payload
        import json

        settings.set_value(db, "update_last_check", json.dumps(payload))
        return payload

    return Updater(
        app_id="newscast",
        root_dir=ROOT_DIR,
        updates_dir=UPDATES_DIR,
        current_version=__version__,
        user_agent="NewsCast-updater",
        backup_fn=backup.write_backup,
        extra_pip=_extra_pip(),
        get_repo=get_repo,
        get_last_check=get_last,
        save_last_check=save_last,
    )


def repo_from_db(db: Session) -> str:
    if env.stonepi_session_secret.strip():
        return normalize_repo(env.github_repo)
    return normalize_repo(settings.get_value(db, "github_repo") or env.github_repo)


def last_check(db: Session) -> dict:
    return _updater(db).last_check()


def check_latest(db: Session, download: bool = True) -> dict:
    return _updater(db).check_latest(download=download)


def install_latest(db: Session) -> dict:
    return _updater(db).install_latest()


def apply_zip(zip_path: Path, expected_tag: str = "") -> dict:
    return _updater().apply_zip(Path(zip_path), expected_tag)


def rollback_code() -> None:
    _updater().rollback_code()


def schedule_restart() -> None:
    _updater().schedule_restart()
