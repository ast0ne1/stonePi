"""Background worker: resumable, verified ZIM downloads and storage moves.

Jobs live in SQLite, so a closed browser, a page change or a service restart
never loses one: on start the worker picks up whatever was in progress and
resumes the partial file with an HTTP Range request.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app import __version__, db
from app.config import env
from app.platform import emit
from app.services import content, helper, kiwix, storage

logger = logging.getLogger("library.downloads")

CHUNK = 1024 * 1024
RETRIES = 6
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


class Cancelled(Exception):
    """The user cancelled: partial file deleted."""


class Stopped(Exception):
    """The service is stopping (restart, backup): leave the job and its partial
    file exactly as they are, so it resumes on the next start."""


def wake() -> None:
    _wake.set()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="library-downloads")
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def running() -> bool:
    return bool(_thread and _thread.is_alive())


def _idle_upkeep(state: dict[str, float]) -> None:
    """While idle: refresh the catalogue daily so update alerts go out on their own."""
    from app.services import catalog

    now = time.monotonic()
    if now - state.get("catalog", 0.0) < 3600:
        return
    state["catalog"] = now
    if catalog.cache_is_stale():
        try:
            content.refresh_catalog(db.get_setting("language", "en") or "en")
        except Exception:
            logger.exception("catalogue refresh failed")


def _loop() -> None:
    upkeep: dict[str, float] = {"catalog": time.monotonic()}  # startup already refreshes
    while not _stop.is_set():
        job = db.next_queued_job()
        if not job:
            _idle_upkeep(upkeep)
            _wake.wait(10)
            _wake.clear()
            continue
        try:
            if job["kind"] == "move":
                _run_move(job)
            else:
                _run_download(job)
        except Stopped:
            return
        except Cancelled:
            _cleanup_partial(job)
            db.update_job(job["id"], status="cancelled", error="")
        except Exception as exc:  # keep the worker alive whatever happens
            logger.exception("job %s failed", job["id"])
            _fail(job, str(exc) or exc.__class__.__name__)


def _fail(job: dict[str, Any], message: str) -> None:
    db.update_job(job["id"], status="failed", error=message[:400])
    if job["kind"] != "move":
        emit(
            "download_failed",
            f"Library: {job['title']} didn’t install",
            message[:300],
            severity="warning",
            dedupe=f"library:download_failed:{job['id']}",
        )


def _check_cancel(job_id: int) -> None:
    if _stop.is_set():
        raise Stopped()
    row = db.get_job(job_id)
    if not row or row["cancel"]:
        raise Cancelled()


def _part_path(job: dict[str, Any]) -> Path:
    return storage.partial_dir() / f"{job['file_name']}.part"


def _cleanup_partial(job: dict[str, Any]) -> None:
    if job["kind"] == "move":
        return
    try:
        _part_path(job).unlink(missing_ok=True)
    except OSError:
        pass


def _throttle_delay(bytes_read: int, started: float) -> float:
    cap = db.get_setting("max_mbps")
    try:
        mbps = float(cap) if cap else 0.0
    except ValueError:
        mbps = 0.0
    if mbps <= 0:
        return 0.0
    expected = bytes_read / (mbps * 1024 * 1024 / 8)
    return max(0.0, expected - (time.monotonic() - started))


def _run_download(job: dict[str, Any]) -> None:
    part = _part_path(job)
    final = storage.content_dir() / job["file_name"]
    kiwix.ensure_content_dir(final.parent)
    part.parent.mkdir(parents=True, exist_ok=True)

    if job["status"] in {"queued", "downloading"}:
        _download(job, part)
        job = db.get_job(job["id"]) or job
    if job["status"] in {"downloading", "verifying"}:
        _verify(job, part)
        job = db.get_job(job["id"]) or job
    if job["status"] == "installing":
        _install(job, part, final)


def _download(job: dict[str, Any], part: Path) -> None:
    _check_cancel(job["id"])  # cancelled while it waited in the queue
    total = int(job["total"] or 0)
    have = part.stat().st_size if part.exists() else 0
    if total and have > total:
        part.unlink()
        have = 0
    db.update_job(job["id"], status="downloading", done=have, error="")
    if total and have == total:
        return
    free = storage.usage(part.parent)["free"]
    if total and free < (total - have) + 512 * 1024 * 1024:
        raise RuntimeError("Not enough free space on the content drive to finish this download.")

    attempt = 0
    last_write = time.monotonic()
    last_cancel = time.monotonic()
    started, session_bytes = time.monotonic(), 0
    headers = {"User-Agent": f"StonePi-Library/{__version__}"}
    while True:
        try:
            req_headers = dict(headers)
            if have:
                req_headers["Range"] = f"bytes={have}-"
            with httpx.Client(timeout=httpx.Timeout(env.request_timeout, read=120), follow_redirects=True) as client:
                with client.stream("GET", job["url"], headers=req_headers) as resp:
                    if have and resp.status_code == 200:
                        # Mirror ignored the Range header: start over.
                        have = 0
                        part.unlink(missing_ok=True)
                    elif resp.status_code not in {200, 206}:
                        raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    if not total:
                        total = int(resp.headers.get("content-length") or 0) + have
                        db.update_job(job["id"], total=total)
                    with part.open("ab") as fh:
                        for chunk in resp.iter_bytes(CHUNK):
                            fh.write(chunk)
                            have += len(chunk)
                            session_bytes += len(chunk)
                            now = time.monotonic()
                            if now - last_write > 1.0:
                                db.update_job(job["id"], done=have)
                                last_write = now
                            if now - last_cancel > 2.0:
                                _check_cancel(job["id"])
                                last_cancel = now
                            delay = _throttle_delay(session_bytes, started)
                            if delay:
                                time.sleep(min(delay, 2.0))
            db.update_job(job["id"], done=have)
            if total and have < total:
                raise httpx.ReadError("connection closed early")
            return
        except (Cancelled, Stopped):
            raise
        except (httpx.HTTPError, OSError) as exc:
            attempt += 1
            if attempt > RETRIES:
                raise RuntimeError(f"Download interrupted ({exc}). Retry to resume where it stopped.") from exc
            logger.info("job %s retry %s after %s", job["id"], attempt, exc)
            db.update_job(job["id"], done=have, error=f"Reconnecting… ({attempt}/{RETRIES})")
            time.sleep(min(60, 5 * attempt))
            _check_cancel(job["id"])


def _verify(job: dict[str, Any], part: Path) -> None:
    db.update_job(job["id"], status="verifying", done=0, error="")
    size = part.stat().st_size
    if job["total"] and size != int(job["total"]):
        part.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file is the wrong size — it was removed. Try installing again.")
    if kiwix.zim_uuid(part) is None:
        part.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file isn’t a ZIM — it was removed.")
    if job["sha256"]:
        digest = hashlib.sha256()
        done = 0
        last = time.monotonic()
        with part.open("rb") as fh:
            while True:
                block = fh.read(8 * CHUNK)
                if not block:
                    break
                digest.update(block)
                done += len(block)
                if time.monotonic() - last > 1.0:
                    db.update_job(job["id"], done=done)
                    _check_cancel(job["id"])
                    last = time.monotonic()
        if digest.hexdigest() != job["sha256"].lower():
            part.unlink(missing_ok=True)
            raise RuntimeError("Checksum didn’t match Kiwix’s — the download was corrupt and has been removed.")
    db.update_job(job["id"], status="installing", done=size)


def _install(job: dict[str, Any], part: Path, final: Path) -> None:
    if part.exists():
        os.replace(part, final)
        try:
            os.chmod(final, 0o640)
        except OSError:
            pass
    if not final.is_file():
        raise RuntimeError("Downloaded file went missing before install.")
    content.finish_install(job, final)
    db.update_job(job["id"], status="done", error="")
    emit(
        "content_installed",
        f"{job['title']} is ready",
        "Open it from the Library on StonePi.",
        audience="household",
        severity="success",
        dedupe=f"library:content_installed:{job['file_name']}",
        url=kiwix.reader_url({"file_name": final.name}),
    )


# ---------- storage moves ----------

def _run_move(job: dict[str, Any]) -> None:
    """Copy every installed file to the new folder, switch, then delete the old copies."""
    target = Path(job["url"])
    kiwix.ensure_content_dir(target)
    rows = [r for r in db.list_content() if r["status"] == "installed"]
    total = sum(int(r["size"]) for r in rows)
    db.update_job(job["id"], status="downloading", total=total, done=0, error="")
    done = 0
    moved: list[tuple[dict[str, Any], Path]] = []
    for row in rows:
        src = Path(row["path"])
        dest = target / row["file_name"]
        if src == dest:
            moved.append((row, dest))
            done += int(row["size"])
            continue
        tmp = target / f".{row['file_name']}.moving"
        with src.open("rb") as fin, tmp.open("wb") as fout:
            last = time.monotonic()
            while True:
                block = fin.read(8 * CHUNK)
                if not block:
                    break
                fout.write(block)
                done += len(block)
                if time.monotonic() - last > 1.0:
                    db.update_job(job["id"], done=done)
                    _check_cancel(job["id"])
                    last = time.monotonic()
        if tmp.stat().st_size != src.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Copy of {row['title']} came out the wrong size.")
        os.replace(tmp, dest)
        moved.append((row, dest))
    db.update_job(job["id"], status="installing", done=total)
    old_kind, old_uuid = db.get_setting("storage_kind"), db.get_setting("storage_uuid")
    old_paths = []
    for row, dest in moved:
        if row["path"] != str(dest):
            old_paths.append(Path(row["path"]))
        db.update_content(row["id"], path=str(dest))
    db.set_setting("storage_path", str(target))
    db.set_setting("storage_kind", db.get_setting("move_kind", "custom"))
    db.set_setting("storage_uuid", db.get_setting("move_uuid", ""))
    ok, data = helper.run("set-storage", str(target))
    if not ok:
        # Files are already in place; the reader keeps waiting for the old
        # mount until the next successful set-storage (reconcile retries it).
        logger.warning("set-storage %s after move failed: %s", target, data.get("error"))
    content.sync()
    for old in old_paths:
        if old.suffix == ".zim":
            try:
                old.unlink(missing_ok=True)
            except OSError:
                logger.warning("couldn't delete old copy %s", old)
    old_partial = old_paths[0].parent / ".partial" if old_paths else None
    if old_partial and old_partial.is_dir() and not any(old_partial.iterdir()):
        shutil.rmtree(old_partial, ignore_errors=True)
    new_uuid = db.get_setting("storage_uuid")
    if old_kind == "drive" and old_uuid and old_uuid != new_uuid:
        # Old drive no longer holds the library: drop its mount entry.
        helper.run("unmount-drive", old_uuid)
    db.update_job(job["id"], status="done", error="")
    from app.services import backup

    backup.recheck("moving the library")
