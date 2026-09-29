"""In-process background jobs for long Dashboard operations.

Daemon thread per job; status lives in a module dict guarded by a lock.
Used so restore / drill / update install do not block the uvicorn event loop
(and therefore /healthz).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "message": job["message"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def start_job(kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Start ``fn(*args, **kwargs)`` on a daemon thread; return job id."""
    job_id = uuid.uuid4().hex
    job: dict[str, Any] = {
        "id": job_id,
        "kind": str(kind or "job"),
        "status": "pending",
        "message": "",
        "started_at": None,
        "finished_at": None,
    }
    with _lock:
        _jobs[job_id] = job

    def _run() -> None:
        with _lock:
            job["status"] = "running"
            job["started_at"] = _now()
        try:
            result = fn(*args, **kwargs)
            message = ""
            if isinstance(result, str):
                message = result
            elif result is not None:
                message = str(result)
            with _lock:
                job["status"] = "ok"
                job["message"] = (message or "Done")[:500]
                job["finished_at"] = _now()
        except Exception as exc:  # noqa: BLE001
            with _lock:
                job["status"] = "error"
                job["message"] = (str(exc) or "Failed")[:500]
                job["finished_at"] = _now()

    threading.Thread(target=_run, name=f"dash-job-{kind}-{job_id[:8]}", daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return _snapshot(job) if job else None


def list_jobs(kind: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        items = [_snapshot(j) for j in _jobs.values()]
    if kind:
        kind_l = kind.strip().lower()
        items = [j for j in items if str(j.get("kind") or "").lower() == kind_l]
    items.sort(key=lambda j: j.get("started_at") or j.get("id") or "", reverse=True)
    return items


def latest_job(kind: str) -> dict[str, Any] | None:
    items = list_jobs(kind)
    return items[0] if items else None
