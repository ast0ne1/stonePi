from __future__ import annotations

import hashlib
import logging
import socket
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from posixpath import dirname, join

import httpx
from sqlalchemy.orm import Session

from app.config import DATA_DIR, LIBRARY_DIR
from app.models import LibraryFile, SyncTask, utcnow
from app.services import settings
from app.services.briefing import BRIEFING_SAVE_RE, enqueue_sync_file, frozen_briefing_path
from app.services.library import pretty_size
from app.services.paper_naming import day_from_briefing_path, paper_download_name

logger = logging.getLogger("newscast.reader_push")
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
MAX_PRUNE_PER_PUSH = 10
# Background push: the scheduler ticks every AUTO_PUSH_SECONDS, but only probes readers
# with something queued, and waits longer between probes while a reader stays asleep.
AUTO_PUSH_SECONDS = 60
AUTO_PROBE_TIMEOUT = 1.5
AUTO_BACKOFF_FAST_MISSES = 3
AUTO_BACKOFF_MAX_SECONDS = 300
AUTO_DUE_SLACK_SECONDS = 5
# Uploads that fail while the reader is awake retry on later ticks, then give up.
MAX_UPLOAD_ATTEMPTS = 5
# Manual pushes wait this long for a push already running to the same reader.
PUSH_WAIT_SECONDS = 30.0
RECENT_HOURS = 12
RECENT_LIMIT = 5

# In-memory reader state (single app process): last probe per host, one push lock per
# host, and the task ids uploading right now.
_probes: dict[str, dict] = {}
_push_locks: dict[str, threading.Lock] = {}
_push_locks_guard = threading.Lock()
_sending: set[str] = set()


class ReaderConflict(RuntimeError):
    """The reader already holds a file NewsCast did not send: retrying will not help."""


def reader_host(db: Session, user_id: int | None = None) -> str:
    if user_id is not None:
        from app.services import reader_config

        return reader_config.reader_host(db, user_id)
    host = (settings.get_value(db, "reader_host") or "").strip()
    host = host.removeprefix("http://").removeprefix("https://").split("/")[0]
    if host:
        return host
    return "" if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_HOST


def reader_upload_dir(db: Session, user_id: int | None = None) -> str:
    if user_id is not None:
        from app.services import reader_config

        return reader_config.reader_upload_dir(db, user_id)
    raw = (settings.get_value(db, "reader_upload_path") or "").strip()
    if not raw:
        raw = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    if not raw.startswith("/"):
        raw = "/" + raw
    fallback = settings.DEFAULT_KOBO_FOLDER if settings.reader_is_kobo(db) else settings.DEFAULT_XTEINK_FOLDER
    return raw.rstrip("/") or fallback


def _http_reachable(host: str, timeout: float) -> bool:
    if not host:
        return False
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=timeout), follow_redirects=True) as client:
            response = client.get(f"http://{host}/api/status")
            return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reader_reachable(
    host: str,
    timeout: float | None = None,
    db: Session | None = None,
    user_id: int | None = None,
) -> bool:
    limit = timeout if timeout is not None else 2.0
    if not host:
        return False
    if db is not None and user_id is not None:
        from app.services import reader_config

        if reader_config.reader_is_kobo(db, user_id):
            return _tcp_reachable(host, reader_config.reader_ssh_port(db, user_id), limit)
        return _http_reachable(host, limit)
    if db is not None and settings.reader_is_kobo(db):
        return _tcp_reachable(host, settings.reader_ssh_port(db), limit)
    return _http_reachable(host, limit)


def _http_ensure_dir(client: httpx.Client, host: str, folder: str) -> None:
    """Create nested folders on CrossPoint (POST /mkdir) if missing."""
    folder = folder if folder.startswith("/") else f"/{folder}"
    if folder in {"", "/"}:
        return
    parts = [part for part in folder.split("/") if part]
    current = ""
    for part in parts:
        parent = current or "/"
        current = f"{current}/{part}"
        listed = client.get(f"http://{host}/api/files", params={"path": current})
        if listed.status_code == 200:
            continue
        created = client.post(
            f"http://{host}/mkdir",
            data={"path": parent, "name": part},
        )
        if created.status_code >= 400 and "already exists" not in (created.text or "").lower():
            detail = (created.text or "").strip().replace("\n", " ")[:120]
            raise RuntimeError(
                f"Could not create folder {current} on {host}"
                + (f": {detail}" if detail else "")
                + ". CrossPoint File Transfer must be on, and the SD card writable."
            )


def _http_upload(host: str, path: Path, dest_dir: str) -> None:
    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    with path.open("rb") as handle:
        with httpx.Client(timeout=UPLOAD_TIMEOUT, follow_redirects=True) as client:
            # Preflight: CrossPoint File Transfer must be reachable.
            try:
                status = client.get(f"http://{host}/api/status")
                if status.status_code >= 500:
                    raise RuntimeError(
                        f"Reader at {host} responded HTTP {status.status_code} on /api/status. "
                        "Turn on CrossPoint File Transfer and try again."
                    )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Could not reach {host} (/api/status). Is CrossPoint File Transfer on?"
                ) from exc

            _http_ensure_dir(client, host, folder)

            def _post(target: str):
                handle.seek(0)
                return client.post(
                    f"http://{host}/upload",
                    params={"path": target},
                    files={"file": (path.name, handle, "application/octet-stream")},
                )

            response = _post(folder)
            # Fallback: if a nested folder still fails, try parent then root.
            if response.status_code >= 400 and "Failed to create file" in (response.text or ""):
                for target in (
                    folder.rsplit("/", 1)[0] if folder.count("/") >= 2 else "",
                    "/",
                ):
                    if not target or target == folder:
                        continue
                    if target != "/":
                        try:
                            _http_ensure_dir(client, host, target)
                        except RuntimeError:
                            continue
                    retry = _post(target)
                    if retry.status_code < 400:
                        return
                    response = retry
            if response.status_code >= 400 and "already exists" in (response.text or "").lower():
                # CrossPoint / CrossInk refuse to overwrite. NewsCast replaces its own
                # uploads before this point, so this file came from somewhere else.
                raise ReaderConflict(
                    f"{path.name} is already on the reader in {folder} but was not sent by NewsCast. "
                    "Delete or rename it on the reader, then send again."
                )
            if response.status_code >= 400:
                detail = (response.text or "").strip().replace("\n", " ")[:160]
                raise RuntimeError(
                    f"Upload to {host}{folder} failed (HTTP {response.status_code})"
                    + (f": {detail}" if detail else "")
                    + ". Check the upload folder exists on the SD card and File Transfer is on."
                )
            response.raise_for_status()


def _http_file_names(host: str, folder: str) -> set[str] | None:
    """Names in a reader folder (GET /api/files), or None when the listing fails."""
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True) as client:
            response = client.get(f"http://{host}/api/files", params={"path": folder or "/"})
        if response.status_code != 200:
            return None
        return {str(item.get("name") or "") for item in response.json() if not item.get("isDirectory")}
    except Exception:  # noqa: BLE001
        return None


def _http_delete(host: str, device_path: str) -> bool:
    """Delete one file on the reader (POST /delete). Missing files count as gone.

    Both CrossPoint and CrossInk also clear the book's saved layout cache on delete.
    """
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=True) as client:
            response = client.post(f"http://{host}/delete", data={"path": device_path})
    except httpx.HTTPError:
        return False
    return response.status_code < 400 or "not found" in (response.text or "").lower()


def detect_firmware(host: str) -> tuple[str, str] | None:
    """Best-effort (family, version) for an Xteink reader, or None when unreachable.

    /api/status only reports a version number, and both firmwares ship a 1.6.0, so
    the family comes from CrossInk's extra web assets (/logo.png). This is a hint:
    the manual Reader firmware setting always wins.
    """
    try:
        with httpx.Client(timeout=httpx.Timeout(3.0, connect=2.0), follow_redirects=True) as client:
            status = client.get(f"http://{host}/api/status")
            if status.status_code != 200:
                return None
            version = str(status.json().get("version") or "")
            logo = client.get(f"http://{host}/logo.png")
    except Exception:  # noqa: BLE001
        return None
    is_crossink = logo.status_code == 200 and logo.headers.get("content-type", "").startswith("image/")
    return ("crossink" if is_crossink else "crosspoint", version)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _device_path(task: SyncTask, default_dir: str) -> str:
    """Where the upload lands on the reader: task folder + the uploaded file's name."""
    return join(_task_folder(task, default_dir), Path(task.file_path).name)


def _previous_uploads(db: Session, task: SyncTask, default_dir: str) -> list[SyncTask]:
    """Earlier completed uploads of the same file that are still on the reader, newest first."""
    target = _device_path(task, default_dir)
    rows = (
        db.query(SyncTask)
        .filter(SyncTask.user_id == task.user_id)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "complete")
        .filter(SyncTask.removed_at.is_(None))
        .filter(SyncTask.id != task.id)
        .order_by(SyncTask.completed_at.desc(), SyncTask.id.desc())
        .all()
    )
    return [row for row in rows if _device_path(row, default_dir) == target]


def _prepare_http_resend(db: Session, host: str, task: SyncTask, digest: str, default_dir: str) -> bool:
    """Clear the way for a re-send of a file NewsCast already put on the reader.

    Returns True when the reader already holds this exact content (skip the upload).
    Otherwise deletes NewsCast's older copy so the same file name can be uploaded
    again — the firmware refuses to overwrite, and delete also resets its cache.
    """
    previous = _previous_uploads(db, task, default_dir)
    if not previous:
        return False
    device_path = _device_path(task, default_dir)
    names = _http_file_names(host, dirname(device_path) or "/")
    now = utcnow()
    if names is not None and Path(device_path).name not in names:
        # Removed on the device since the last push — upload fresh.
        for row in previous:
            row.removed_at = now
        return False
    if names is not None and previous[0].content_hash == digest:
        return True
    if not _http_delete(host, device_path):
        raise RuntimeError(
            f"Could not replace {Path(device_path).name} on the reader. "
            "Check File Transfer is on, then send again."
        )
    for row in previous:
        row.removed_at = now
    return False


def prune_reader_papers(
    db: Session,
    host: str,
    user_id: int,
    *,
    today: date | None = None,
) -> int:
    """Delete this account's older NewsCast papers from the reader (Keep papers on reader).

    Only files NewsCast itself uploaded (completed tasks) are touched, never Send /
    library files, and today's and yesterday's papers always stay.
    """
    from app.services import reader_config
    from app.services.delivery import briefing_day_for_task

    keep = reader_config.reader_keep_days(db, user_id)
    if keep <= 0:
        return 0
    keep = max(keep, settings.MIN_READER_KEEP_DAYS)
    current = today or datetime.now().astimezone().date()
    cutoff = current - timedelta(days=keep - 1)
    default_dir = reader_upload_dir(db, user_id=user_id)
    stale: dict[str, list[SyncTask]] = {}
    rows = (
        db.query(SyncTask)
        .filter(SyncTask.user_id == int(user_id))
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "complete")
        .filter(SyncTask.removed_at.is_(None))
        .all()
    )
    for row in rows:
        if _is_library_task(row):
            continue
        day = briefing_day_for_task(row)
        if day is None or day >= cutoff:
            continue
        stale.setdefault(_device_path(row, default_dir), []).append(row)
    removed = 0
    now = utcnow()
    # Oldest first, a few per push: history from before this setting existed can hold
    # many papers, and each delete is a round-trip to the reader.
    ordered = sorted(stale.items(), key=lambda item: min(briefing_day_for_task(row) for row in item[1]))
    for device_path, group in ordered[:MAX_PRUNE_PER_PUSH]:
        if not _http_delete(host, device_path):
            # Reader likely went to sleep mid-prune; try again on the next push.
            logger.warning("prune stopped at %s on %s", device_path, host)
            break
        for row in group:
            row.removed_at = now
        removed += 1
    return removed


def _known_hosts_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "reader_known_hosts"


def _ensure_sftp_dir(sftp, folder: str) -> None:
    parts = [part for part in folder.split("/") if part]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def _sftp_upload(db: Session, host: str, path: Path, dest_dir: str, user_id: int | None = None) -> None:
    import paramiko

    from app.services import reader_config

    folder = dest_dir if dest_dir.startswith("/") else f"/{dest_dir}"
    if user_id is not None:
        port = reader_config.reader_ssh_port(db, user_id)
        user = reader_config.reader_ssh_user(db, user_id)
        password = reader_config.reader_ssh_password(db, user_id)
    else:
        port = settings.reader_ssh_port(db)
        user = settings.reader_ssh_user(db)
        password = settings.get_value(db, "reader_ssh_password")
    client = paramiko.SSHClient()
    keys = _known_hosts_path()
    client.load_system_host_keys()
    if keys.exists():
        client.load_host_keys(str(keys))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password or None,
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
            auth_timeout=20,
        )
        client.save_host_keys(str(keys))
        sftp = client.open_sftp()
        try:
            _ensure_sftp_dir(sftp, folder)
            sftp.put(str(path), f"{folder.rstrip('/')}/{path.name}")
        finally:
            sftp.close()
    finally:
        client.close()


def upload_file(host: str, path: Path, dest_dir: str, db: Session | None = None, user_id: int | None = None) -> None:
    if db is not None and user_id is not None:
        from app.services import reader_config

        if reader_config.reader_is_kobo(db, user_id):
            _sftp_upload(db, host, path, dest_dir, user_id=user_id)
            return
        _http_upload(host, path, dest_dir)
        return
    if db is not None and settings.reader_is_kobo(db):
        _sftp_upload(db, host, path, dest_dir)
        return
    _http_upload(host, path, dest_dir)


def pending_crosspoint(db: Session, user_id: int | None = None) -> list[SyncTask]:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status == "pending")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    return query.order_by(SyncTask.created_at.asc()).all()


def _is_library_task(task: SyncTask) -> bool:
    """Library/Send files live under LIBRARY_DIR — never label them as today's paper."""
    file_path = (task.file_path or "").replace("\\", "/")
    library_root = str(LIBRARY_DIR).replace("\\", "/")
    if library_root and file_path.startswith(library_root.rstrip("/") + "/"):
        return True
    if "/library/" in file_path.lower():
        return True
    return False


def queue_label(task: SyncTask) -> str:
    from app.services.delivery import briefing_day_for_task

    name = Path(task.save_path or task.file_path).name
    stem = Path(name).stem or name
    if _is_library_task(task):
        return f"File · {stem}"
    day = briefing_day_for_task(task)
    today = datetime.now().astimezone().date()
    if day == today:
        return "Today's paper"
    if day is not None:
        return f"Paper · {day.strftime('%d %b %Y')}"
    return f"File · {stem}"


def _queue_display_sort_key(task: SyncTask) -> tuple:
    """Today's paper first, then older papers (newest date first), then Send files (newest queued first)."""
    from app.services.delivery import briefing_day_for_task

    today = datetime.now().astimezone().date()
    day = briefing_day_for_task(task)
    created = task.created_at or datetime.min.replace(tzinfo=timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    # Negate timestamps so newer sorts first within a group.
    created_rank = -created.timestamp()
    if day == today:
        return (0, created_rank)
    if day is not None:
        return (1, -day.toordinal(), created_rank)
    return (2, created_rank)


def _created_label(value: datetime | None) -> str:
    if value is None:
        return ""
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%d %b %Y %H:%M") + " UTC"


def _clock(value: datetime | None) -> str:
    """Local HH:MM for queue status lines (SQLite hands back naive UTC)."""
    if value is None:
        return ""
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%H:%M")


def _auto_push_on(db: Session, user_id: int | None) -> bool:
    if user_id is None:
        return settings.reader_push_enabled(db)
    from app.services import reader_config

    return reader_config.reader_push_enabled(db, int(user_id))


def next_try_at(host: str) -> datetime | None:
    """When the background push will next look for this reader (None before the first probe)."""
    probe = last_probe(host)
    if probe is None:
        return None
    due = probe.get("next_try_at")
    if due is not None:
        return due
    at = probe.get("at")
    return at + timedelta(seconds=AUTO_PUSH_SECONDS) if at is not None else None


def queue_status(task: SyncTask, host: str, *, auto_on: bool) -> tuple[str, str]:
    """(state, short label) for one waiting task: sending, retry, or waiting."""
    if task.task_id in _sending:
        return "sending", "Sending…"
    probe = last_probe(host)
    next_label = _clock(next_try_at(host)) if auto_on else ""
    if (task.attempts or 0) > 0 and (task.error_message or "").strip():
        label = f"Failed: {task.error_message.strip()}"
        if auto_on:
            label += f" · will retry {next_label}" if next_label else " · will retry"
        else:
            label += " · Push now to retry"
        return "retry", label
    label = "Waiting for reader"
    if probe is not None and probe.get("at") is not None:
        seen = "online" if probe.get("online") else "asleep"
        label += f" · last tried {_clock(probe['at'])} ({seen})"
    if next_label:
        label += f" · next try {next_label}"
    return "waiting", label


def queue_items(db: Session, user_id: int | None = None) -> list[dict]:
    host = reader_host(db, user_id=user_id)
    auto_on = _auto_push_on(db, user_id)
    items = []
    for task in sorted(pending_crosspoint(db, user_id=user_id), key=_queue_display_sort_key):
        name = Path(task.save_path or task.file_path).name
        state, status_label = queue_status(task, host, auto_on=auto_on)
        items.append(
            {
                "task_id": task.task_id,
                "label": queue_label(task),
                "name": name,
                "size_label": pretty_size(task.size or 0),
                "created_label": _created_label(task.created_at),
                "state": state,
                "status_label": status_label,
            }
        )
    return items


def recent_items(db: Session, user_id: int | None = None, *, now: datetime | None = None) -> list[dict]:
    """Reader uploads that finished (sent or gave up) in the last few hours, newest first."""
    cutoff = (now or utcnow()) - timedelta(hours=RECENT_HOURS)
    query = (
        db.query(SyncTask)
        .filter(SyncTask.kind == "crosspoint")
        .filter(SyncTask.status.in_(("complete", "failed")))
        .filter(SyncTask.completed_at.isnot(None))
        .filter(SyncTask.completed_at >= cutoff)
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    items = []
    for task in query.order_by(SyncTask.completed_at.desc(), SyncTask.id.desc()).limit(RECENT_LIMIT).all():
        if task.status == "complete":
            state, status_label = "sent", f"Sent {_clock(task.completed_at)}"
        else:
            state, status_label = "failed", f"Failed: {(task.error_message or 'unknown error').strip()}"
        items.append(
            {
                "task_id": task.task_id,
                "label": queue_label(task),
                "name": Path(task.save_path or task.file_path).name,
                "state": state,
                "status_label": status_label,
            }
        )
    return items


def cancel_pending(db: Session, task_id: str, user_id: int | None = None) -> bool:
    query = (
        db.query(SyncTask)
        .filter(SyncTask.task_id == task_id)
        .filter(SyncTask.status == "pending")
    )
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    task = query.first()
    if task is None:
        return False
    task.status = "cancelled"
    task.completed_at = utcnow()
    db.commit()
    return True


def enqueue_frozen_briefing(db: Session, user_id: int | None = None) -> SyncTask | None:
    """Queue today's frozen EPUB for CrossPoint/Kobo push.

    Skips when the paper file is missing or has no stories (empty shell).
    """
    from app.services.briefing import current_stories

    uid = int(user_id or 1)
    path = frozen_briefing_path("today", suffix="epub", fallback=False, user_id=uid)
    if path is None:
        return None
    if not list(current_stories(db, day="today", user_id=uid)):
        logger.info("skip empty briefing enqueue user_id=%s path=%s", uid, path.name)
        return None
    dest = reader_upload_dir(db, user_id=uid)
    day = day_from_briefing_path(path.stem) or datetime.now().date()
    save_name = paper_download_name(db, day, suffix="epub")
    return enqueue_sync_file(
        db,
        path,
        save_name,
        kind="crosspoint",
        save_path=join(dest, save_name),
        user_id=uid,
    )


def enqueue_briefing_and_library(
    db: Session,
    user_id: int | None = None,
    *,
    include_briefing: bool = True,
    include_library: bool = True,
) -> list[SyncTask]:
    """Queue today's frozen paper and/or Send library files for the reader.

    Missing frozen papers are skipped (no error) when include_briefing is True.
    """
    from app.services.library import enqueue_library_file, library_path

    uid = int(user_id or 1)
    tasks: list[SyncTask] = []
    if include_briefing:
        briefing_task = enqueue_frozen_briefing(db, user_id=uid)
        if briefing_task:
            tasks.append(briefing_task)
    if include_library:
        for item in (
            db.query(LibraryFile)
            .filter(LibraryFile.user_id == uid)
            .order_by(LibraryFile.created_at.desc())
            .all()
        ):
            path = library_path(item)
            if not path.exists():
                continue
            tasks.append(enqueue_library_file(db, item))
    return tasks


def _task_folder(task: SyncTask, default: str) -> str:
    folder = dirname(task.save_path or "")
    return folder if folder and folder != "." else default


def _push_lock(host: str) -> threading.Lock:
    with _push_locks_guard:
        return _push_locks.setdefault(host, threading.Lock())


def push_in_progress(host: str) -> bool:
    lock = _push_locks.get(host)
    return bool(lock and lock.locked())


def flush_pending(
    db: Session,
    user_id: int | None = None,
    *,
    wait: bool = True,
    probe_timeout: float | None = None,
) -> dict:
    """Upload everything queued for this account, one push per reader at a time.

    ``wait=False`` (the background tick) skips instead of queueing behind a push that
    is already running to the same reader.
    """
    uid = int(user_id) if user_id is not None else None
    host = reader_host(db, user_id=uid)
    lock = _push_lock(host)
    acquired = lock.acquire(timeout=PUSH_WAIT_SECONDS) if wait else lock.acquire(blocking=False)
    if not acquired:
        probe = last_probe(host) or {}
        return {
            "ok": False,
            "busy": True,
            "online": bool(probe.get("online")),
            "uploaded": 0,
            "pending": len(pending_crosspoint(db, user_id=uid)),
            "host": host,
        }
    try:
        return _flush_locked(db, uid, host, probe_timeout)
    finally:
        lock.release()


def _flush_locked(db: Session, uid: int | None, host: str, probe_timeout: float | None) -> dict:
    from app.services.delivery import briefing_day_for_task, mark_briefing_pushed

    dest = reader_upload_dir(db, user_id=uid) if uid is not None else reader_upload_dir(db)
    tasks = pending_crosspoint(db, user_id=uid)
    online = reader_reachable(host, timeout=probe_timeout, db=db, user_id=uid)
    remember_probe(host, online)
    if not online:
        return {"ok": False, "online": False, "uploaded": 0, "pending": len(tasks), "host": host}
    uploaded = 0
    skipped = 0
    errors: list[str] = []
    today = datetime.now().astimezone().date()
    http_reader = uid is None or not _is_kobo(db, uid)
    # Detection and pruning ride along with real pushes only — the background tick
    # calls this every minute and the reader's web server is a small ESP32.
    per_push_upkeep = bool(tasks) and uid is not None and http_reader
    if per_push_upkeep:
        _remember_firmware(db, host, uid)
    for task in tasks:
        path = Path(task.file_path)
        if not path.exists():
            task.status = "failed"
            task.error_message = "File missing before upload."
            task.completed_at = utcnow()
            db.commit()
            continue
        task_uid = getattr(task, "user_id", None) or uid
        _sending.add(task.task_id)
        try:
            digest = _file_digest(path)
            # CrossPoint / CrossInk refuse to overwrite: skip unchanged re-sends and
            # delete NewsCast's own older copy before sending a changed one.
            unchanged = bool(uid is not None and http_reader and _prepare_http_resend(db, host, task, digest, dest))
            if not unchanged:
                upload_file(host, path, _task_folder(task, dest), db=db, user_id=task_uid)
            task.status = "complete"
            task.error_message = None
            task.completed_at = utcnow()
            task.attempts = (task.attempts or 0) + 1
            task.last_attempt_at = task.completed_at
            task.content_hash = digest
            if unchanged:
                skipped += 1
                if briefing_day_for_task(task) == today:
                    mark_briefing_pushed(db, today)
                db.commit()
                continue
            uploaded += 1
            if briefing_day_for_task(task) == today:
                mark_briefing_pushed(db, today)
                from app.services import ntfy
                from app.services.paper_naming import paper_display_title

                label = Path(task.save_path or path.name).name or paper_display_title(db, today)
                instance = (settings.get_value(db, "instance_name") or "").strip() or "NewsCast"
                ntfy.notify(
                    db,
                    kind="push",
                    title=instance,
                    body=f"Morning paper is on the reader — {label}",
                    user_id=getattr(task, "user_id", None),
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("upload failed for %s: %s", path.name, exc)
            task.last_attempt_at = utcnow()
            if not isinstance(exc, ReaderConflict) and not reader_reachable(
                host, timeout=probe_timeout, db=db, user_id=uid
            ):
                # The reader went to sleep mid-push: not the file's fault, so it keeps
                # waiting and the rest of the queue goes out on the next try.
                remember_probe(host, False)
                db.commit()
                break
            reason = str(exc)[:240]
            errors.append(reason)
            task.attempts = (task.attempts or 0) + 1
            task.error_message = reason
            if isinstance(exc, ReaderConflict) or task.attempts >= MAX_UPLOAD_ATTEMPTS:
                task.status = "failed"
                task.completed_at = task.last_attempt_at
            db.commit()
        finally:
            _sending.discard(task.task_id)
    db.commit()
    removed = 0
    if per_push_upkeep:
        removed = prune_reader_papers(db, host, uid, today=today)
        db.commit()
    pending = len(pending_crosspoint(db, user_id=uid))
    return {
        "ok": True,
        "online": True,
        "uploaded": uploaded,
        "skipped": skipped,
        "removed": removed,
        "pending": pending,
        "errors": errors,
        "host": host,
    }


def _is_kobo(db: Session, user_id: int) -> bool:
    from app.services import reader_config

    return reader_config.reader_is_kobo(db, user_id)


def _remember_firmware(db: Session, host: str, user_id: int) -> None:
    from app.services import reader_config

    found = detect_firmware(host)
    if found:
        reader_config.remember_detected_firmware(db, user_id, *found)


def _push_users(db: Session) -> list:
    """Active accounts with Push when the reader is on Wi-Fi turned on and a reader host."""
    from app.models import User
    from app.services import reader_config

    users = db.query(User).filter(User.active.is_(True)).order_by(User.id.asc()).all()
    return [
        user
        for user in users
        if reader_config.reader_push_enabled(db, user.id) and reader_config.reader_host(db, user.id)
    ]


def flush_all_enabled(db: Session) -> list[dict]:
    """Flush each active user who has push-when-online and a host (no backoff)."""
    return [flush_pending(db, user_id=user.id) for user in _push_users(db)]


def auto_push(db: Session, *, now: datetime | None = None) -> list[dict]:
    """Scheduler tick: send each account's queue as soon as its reader answers.

    Readers with nothing queued are never probed, a reader that stays asleep is probed
    less often (see ``_backoff_seconds``), and a push already running to that reader is
    left alone rather than doubled up.
    """
    when = now or utcnow()
    results: list[dict] = []
    for user in _push_users(db):
        uid = int(user.id)
        if not pending_crosspoint(db, user_id=uid):
            continue
        host = reader_host(db, user_id=uid)
        if push_in_progress(host):
            continue
        due = (last_probe(host) or {}).get("next_try_at")
        if due is not None and when + timedelta(seconds=AUTO_DUE_SLACK_SECONDS) < due:
            continue
        results.append(flush_pending(db, user_id=uid, wait=False, probe_timeout=AUTO_PROBE_TIMEOUT))
    return results


def _backoff_seconds(misses: int) -> int:
    """Every tick for the first few misses, then doubling up to a five-minute gap."""
    if misses <= AUTO_BACKOFF_FAST_MISSES:
        return AUTO_PUSH_SECONDS
    return min(AUTO_PUSH_SECONDS * 2 ** (misses - AUTO_BACKOFF_FAST_MISSES), AUTO_BACKOFF_MAX_SECONDS)


def remember_probe(host: str, online: bool) -> None:
    when = utcnow()
    misses = 0 if online else int((_probes.get(host) or {}).get("misses") or 0) + 1
    _probes[host] = {
        "host": host,
        "online": bool(online),
        "at": when,
        "misses": misses,
        "next_try_at": None if online else when + timedelta(seconds=_backoff_seconds(misses)),
    }


def last_probe(host: str) -> dict | None:
    return _probes.get(host)


def reset_probe_state() -> None:
    """Forget remembered probes and in-flight uploads (tests)."""
    _probes.clear()
    _sending.clear()


def snapshot(db: Session, *, probe: bool = True, user_id: int | None = None) -> dict:
    from app.services import reader_config

    uid = int(user_id) if user_id is not None else None
    host = reader_host(db, user_id=uid)
    pending = pending_crosspoint(db, user_id=uid)
    if probe:
        online = reader_reachable(host, timeout=0.6, db=db, user_id=uid)
        remember_probe(host, online)
        checked = True
    else:
        prev = last_probe(host)
        online = None if prev is None else prev["online"]
        checked = prev is not None
    if uid is not None:
        push_on = reader_config.reader_push_enabled(db, uid)
        device = reader_config.reader_device(db, uid)
        ssh_port = reader_config.reader_ssh_port(db, uid)
        upload_path = reader_upload_dir(db, user_id=uid)
    else:
        push_on = settings.reader_push_enabled(db)
        device = settings.reader_device(db)
        ssh_port = settings.reader_ssh_port(db)
        upload_path = reader_upload_dir(db)
    probe_at = (last_probe(host) or {}).get("at")
    return {
        "host": host,
        "upload_path": upload_path,
        "online": online,
        "checked": checked,
        "checked_label": _clock(probe_at),
        "sending": push_in_progress(host),
        "pending": len(pending),
        "queue": queue_items(db, user_id=uid),
        "recent": recent_items(db, user_id=uid),
        "push_when_online": push_on,
        "device": device,
        "ssh_port": ssh_port,
        "active_label": queue_label(pending[0]) if pending else "",
    }


def recent_sync_result(db: Session, user_id: int | None = None) -> dict | None:
    query = db.query(SyncTask).filter(SyncTask.status.in_(("complete", "failed")))
    if user_id is not None:
        query = query.filter(SyncTask.user_id == int(user_id))
    task = query.order_by(SyncTask.completed_at.desc(), SyncTask.id.desc()).first()
    if task is None:
        return None
    return {
        "status": task.status,
        "label": queue_label(task),
        "error": (task.error_message or "").strip(),
        "task_id": task.task_id,
        "completed_at": task.completed_at.isoformat() if task.completed_at else "",
    }
