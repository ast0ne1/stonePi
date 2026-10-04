"""Installed content: install/update/remove/move, reconcile, and the backup manifest."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from app import db
from app.config import BACKUP_MANIFEST, DEFAULT_CONTENT_DIR, GIB
from app.platform import emit
from app.services import backup, catalog, kiwix, storage

logger = logging.getLogger("library.content")
_manifest_lock = threading.Lock()


def write_manifest() -> None:
    """What stonepi-backup copies (and restore puts back)."""
    rows = [r for r in db.list_content() if r["status"] == "installed"]
    payload = {
        "content_dir": str(storage.content_dir()),
        "files": [
            {k: r[k] for k in ("file_name", "path", "size", "sha256", "title", "name", "flavour", "issued", "book_id")}
            for r in rows
        ],
    }
    BACKUP_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with _manifest_lock:
        tmp = BACKUP_MANIFEST.with_name(f".backup-manifest.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(BACKUP_MANIFEST)


def sync() -> tuple[bool, str]:
    """After any content change: manifest, Kiwix library, backup re-check."""
    write_manifest()
    return kiwix.apply_library()


def _relocated(row: dict[str, Any]) -> Path | None:
    """Same file (name + size) in the current or default folder, e.g. after a
    restore put content on the microSD because the drive wasn't connected."""
    for folder in (storage.content_dir(), DEFAULT_CONTENT_DIR):
        candidate = folder / row["file_name"]
        try:
            if candidate.is_file() and candidate.stat().st_size == int(row["size"]):
                return candidate
        except OSError:
            continue
    return None


def reconcile() -> bool:
    """Mark files missing (drive unplugged, restore without content) or found again."""
    changed = False
    relocated_to_default = False
    for row in db.list_content():
        if row["status"] not in {"installed", "missing"}:
            continue
        path = Path(row["path"])
        present = path.is_file()
        if not present:
            moved = _relocated(row)
            if moved:
                db.update_content(row["id"], path=str(moved))
                relocated_to_default = relocated_to_default or moved.parent == DEFAULT_CONTENT_DIR
                present = True
                changed = True
        status = "installed" if present else "missing"
        if status != row["status"]:
            db.update_content(row["id"], status=status)
            changed = True
    if relocated_to_default and not storage.content_dir().exists():
        # The external folder is gone and content now lives on the microSD.
        db.set_setting("storage_path", str(DEFAULT_CONTENT_DIR))
        db.set_setting("storage_kind", "sd")
        db.set_setting("storage_uuid", "")
        # Drop the reader's wait on the old drive's mount.
        from app.services import helper

        helper.run("set-storage", str(DEFAULT_CONTENT_DIR))
    if changed:
        sync()
        missing = [r for r in db.list_content() if r["status"] == "missing"]
        if missing:
            emit(
                "storage_missing",
                "Library storage not connected",
                f"{len(missing)} collection{'s' if len(missing) != 1 else ''} can’t be found in {storage.content_dir()}.",
                severity="warning",
                dedupe=f"library:storage_missing:{date.today().isoformat()}",
            )
    return changed


def refresh_catalog(lang: str) -> tuple[bool, str]:
    """Refresh the catalogue, then tell admins about new versions (once each)."""
    ok, message = catalog.refresh(lang, installed_names())
    if ok:
        rows = {r["id"]: r for r in db.list_content()}
        for content_id, latest in updates_available().items():
            row = rows[content_id]
            emit(
                "update_available",
                f"Library: new {row['title']} available",
                f"{latest['issued'][:7]} edition ({latest['size'] // GIB or 1} GB). Update it from the Library.",
                dedupe=f"library:update_available:{latest['file_name']}",
            )
    return ok, message


_refresh_lock = threading.Lock()
_refreshing: set[str] = set()


def refresh_catalog_in_background(lang: str) -> bool:
    """Start a background refresh for ``lang``; False if one is already running.

    Browse calls this on every visit while the cache is stale, so without the
    guard a slow catalogue fetch piles up a thread per page load. Keyed by
    language so a language change mid-refresh still fetches its own titles.
    """
    with _refresh_lock:
        if lang in _refreshing:
            return False
        _refreshing.add(lang)

    def run() -> None:
        try:
            refresh_catalog(lang)
        except Exception:  # noqa: BLE001 - a failed refresh must still clear the guard
            logger.exception("Catalogue refresh failed")
        finally:
            with _refresh_lock:
                _refreshing.discard(lang)

    try:
        threading.Thread(target=run, daemon=True, name="catalog-refresh").start()
    except Exception:
        with _refresh_lock:
            _refreshing.discard(lang)
        raise
    return True


def installed_names() -> list[str]:
    return sorted({r["name"] for r in db.list_content()})


def find_installed(name: str, flavour: str) -> dict[str, Any] | None:
    for row in db.list_content():
        if row["name"] == name and (row["flavour"] or "") == (flavour or ""):
            return row
    return None


def updates_available() -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in db.list_content():
        latest = catalog.newest(row["name"], row["flavour"])
        if latest and latest["issued"] > (row["issued"] or "") and latest["file_name"] != row["file_name"]:
            out[row["id"]] = latest
    return out


def enqueue(entry: dict[str, Any], *, replaces: dict[str, Any] | None = None, in_place: bool = False) -> tuple[int | None, str]:
    """Queue a download for a catalogue entry (resolves size + checksum first)."""
    existing = db.job_for_name(entry["name"], entry.get("flavour") or "")
    if existing:
        return existing["id"], "Already downloading"
    try:
        meta = catalog.resolve_download(entry["meta4"])
    except Exception as exc:  # network / metalink parse
        logger.warning("resolve %s failed: %s", entry.get("meta4"), exc)
        return None, "Couldn’t reach the Kiwix download server — try again later."
    size = meta["size"] or int(entry.get("size") or 0)
    need = backup.install_need(size)
    if replaces and in_place:
        # Replace in place: the old copy is deleted first, so its space counts.
        if need["free"] + int(replaces["size"]) < need["need"]:
            return None, _space_message(need)
    elif replaces and not need["ok"]:
        # Safe swap keeps old and new side by side until the switch.
        return None, "needs_in_place"
    elif not need["ok"]:
        return None, _space_message(need)
    job_id = db.add_job(
        {
            "kind": "update" if replaces else "install",
            "name": entry["name"],
            "flavour": entry.get("flavour") or "",
            "title": entry.get("title") or entry["name"],
            "summary": entry.get("summary") or "",
            "language": entry.get("language") or "",
            "issued": entry.get("issued") or "",
            "book_id": entry.get("book_id") or "",
            "url": meta["url"],
            "file_name": meta["file_name"],
            "total": size,
            "sha256": meta["sha256"],
            "replaces_id": replaces["id"] if replaces else None,
            "in_place": 1 if in_place else 0,
        }
    )
    if in_place and replaces:
        # Not enough room for both: drop the old copy now (user confirmed).
        remove(int(replaces["id"]), reason="update")
    from app.services import downloads

    downloads.wake()
    return job_id, "Download queued"


def _space_message(need: dict[str, Any]) -> str:
    gb = lambda n: f"{n / GIB:.1f} GB"  # noqa: E731
    if need["doubled"]:
        return (
            f"Not enough space: needs {gb(need['size'])} + {gb(need['size'])} for the backup copy on the same drive "
            f"(plus margin), {gb(need['free'])} free."
        )
    return f"Not enough space: needs {gb(need['size'])} plus margin, {gb(need['free'])} free."


def remove(content_id: int, *, reason: str = "removal") -> tuple[bool, str]:
    row = db.get_content(content_id)
    if not row:
        return False, "Not found"
    db.update_content(content_id, status="removing")
    path = Path(row["path"])
    # Only ever delete the exact file the Library recorded — never a folder.
    if path.suffix == ".zim" and path.name == row["file_name"]:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            db.update_content(content_id, status="installed")
            return False, f"Couldn’t delete the file ({exc.strerror or exc})"
    db.delete_content(content_id)
    sync()
    backup.recheck(reason)
    return True, f"Removed {row['title']}"


def finish_install(job: dict[str, Any], final: Path) -> int:
    book_id = kiwix.zim_uuid(final) or job["book_id"]
    content_id = db.add_content(
        {
            "book_id": book_id,
            "name": job["name"],
            "flavour": job["flavour"],
            "title": job["title"],
            "summary": job["summary"],
            "language": job["language"],
            "issued": job["issued"],
            "file_name": final.name,
            "path": str(final),
            "size": final.stat().st_size,
            "sha256": job["sha256"],
            "status": "installed",
        }
    )
    old = db.get_content(int(job["replaces_id"])) if job.get("replaces_id") else None
    if old and old["path"] != str(final):
        remove(int(old["id"]), reason="update")
    sync()
    backup.recheck("installing new content" if job["kind"] == "install" else "an update")
    return content_id
