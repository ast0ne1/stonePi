"""Kiwix reader: install state, the library.xml it serves, and health."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

from app import db
from app.config import LIBRARY_XML, env
from app.services import helper

logger = logging.getLogger("library.kiwix")

ZIM_MAGIC = b"ZIM\x04"  # 0x044D495A little-endian
_status_cache: dict[str, Any] = {"at": 0.0, "value": None}
# Request threads and the download worker both rewrite library.xml.
_write_lock = threading.Lock()


def status(*, fresh: bool = False) -> dict[str, Any]:
    """Install/unit state from the helper (cached briefly; it shells out)."""
    # Every change calls forget_status(); the cache only spares sudo on reads
    # (Dashboard polls the tile through /api/display).
    if not fresh and _status_cache["value"] is not None and time.monotonic() - _status_cache["at"] < 60:
        return _status_cache["value"]
    ok, data = helper.run("status", timeout=20)
    value = {
        "installed": bool(data.get("kiwix_installed")) if ok else False,
        "version": data.get("kiwix_version", "") if ok else "",
        "active": bool(data.get("kiwix_active")) if ok else False,
        "include_content": bool(data.get("include_content")) if ok else False,
        "simulated": helper.simulated(),
        "error": "" if ok else data.get("error", "Helper unavailable"),
    }
    _status_cache.update(at=time.monotonic(), value=value)
    return value


def forget_status() -> None:
    _status_cache["value"] = None


def reachable() -> bool:
    if helper.simulated():
        return status()["active"]
    try:
        resp = httpx.get(f"{env.kiwix_url.rstrip('/')}{env.reader_path}/", timeout=2.5)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


def zim_uuid(path: Path) -> str | None:
    """Book id from the ZIM header; None when the file isn't a ZIM."""
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:4] != ZIM_MAGIC:
        return None
    return str(uuid.UUID(bytes=head[8:24]))


def _group_readable(path: Path, mode: int) -> None:
    # stonepi-kiwix reads via the stonepi-library group; the unit's umask is tighter.
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_content_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _group_readable(path, 0o2750)


def book_slug(row: dict[str, Any]) -> str:
    # kiwix-serve names each book after its file (minus .zim).
    return Path(row["file_name"]).stem


def reader_url(row: dict[str, Any] | None = None) -> str:
    base = env.reader_path.rstrip("/")
    if row is None:
        return f"{base}/"
    return f"{base}/content/{book_slug(row)}"


def write_library_xml() -> int:
    """Rebuild library.xml from installed content; returns the book count.

    The Library database is the source of truth, so the file is regenerated
    rather than edited; kiwix-serve reloads it (--monitorLibrary).
    """
    books = [r for r in db.list_content() if r["status"] == "installed"]
    root = ET.Element("library", version="20110515")
    for r in books:
        ET.SubElement(
            root,
            "book",
            id=r["book_id"],
            path=r["path"],
            title=r["title"],
            description=r["summary"],
            language=r["language"],
            name=r["name"],
            flavour=r["flavour"],
            date=r["issued"],
            size=str(int(r["size"]) // 1024),
        )
    LIBRARY_XML.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        tmp = LIBRARY_XML.with_name(f".library.xml.{uuid.uuid4().hex[:8]}")
        ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
        _group_readable(tmp, 0o640)
        tmp.replace(LIBRARY_XML)
    return len(books)


def ensure_running() -> None:
    """On start (e.g. after a restore or reboot): content but no reader → start it."""
    if not any(r["status"] == "installed" for r in db.list_content()):
        return
    current = status(fresh=True)
    if current["installed"] and not current["active"]:
        write_library_xml()
        helper.run("kiwix", "start")
        forget_status()


def apply_library() -> tuple[bool, str]:
    """Write library.xml, then run Kiwix only while there is something to serve."""
    count = write_library_xml()
    if not status()["installed"]:
        return True, ""
    ok, data = helper.run("kiwix", "restart" if count else "stop")
    forget_status()
    return ok, "" if ok else data.get("error", "Couldn’t restart the reader")
