#!/usr/bin/env python3
"""Copy Library ZIM files into the backup drive's shared content store (as root).

Called by stonepi-backup (USB mode, after apps restart) when
INCLUDE_LIBRARY_CONTENT=1. One store per drive, beside — not inside —
RaspberryPi-Backup/ (so it never shows up as a backup): only new or updated
ZIMs are copied, verified against the Library's recorded size + SHA-256, and
files no longer installed are pruned.

The store may sit on the same drive as the content: that is a full second
copy, which the user chose in Library → Settings → Backup.

The manifest is written by the unprivileged Library app, so nothing in it is
trusted: names must be plain ``*.zim`` file names, sources must be real files
under the Library's storage roots, and every open refuses symlinks.

  stonepi-backup-library backup  MANIFEST STORE [LOG]   → "ok|skipped|failed BYTES"
  stonepi-backup-library restore STORE MANIFEST [LOG]   → "ok|failed BYTES TARGET"
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

GIB = 1024**3
APP_USER = "stonepi-library"
DEFAULT_TARGET = "/var/lib/stonepi/library/zim"
ALLOWED_ROOTS = ("/var/lib/stonepi/library/zim", "/mnt/", "/media/", "/srv/")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.zim$")
CHUNK = 8 * 1024 * 1024
_log_path = ""


def log(msg: str) -> None:
    line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
    if _log_path:
        try:
            with open(_log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return
        except OSError:
            pass
    print(line, file=sys.stderr)


def safe_dir(path: str) -> bool:
    if not SAFE_PATH.match(path) or "/.." in path or "//" in path:
        return False
    if "RaspberryPi-Backup" in path or "StonePi-Library-Backup" in path:
        return False
    # The backup drive holds every app's data: only its Library folder.
    if path.startswith("/mnt/stonepi-backup/") and not (
        path == "/mnt/stonepi-backup/StonePi-Library" or path.startswith("/mnt/stonepi-backup/StonePi-Library/")
    ):
        return False
    return path == DEFAULT_TARGET or path.startswith(DEFAULT_TARGET + "/") or any(
        path.startswith(r) and path.count("/") >= 3 for r in ALLOWED_ROOTS[1:]
    )


def open_dir(path: str, *, create: bool = False, owner: tuple[int, int] | None = None) -> int:
    """fd for ``path``, walking from / with O_NOFOLLOW: no component may be a link.

    Folders it creates are handed to ``owner`` so the Library (and the reader)
    can get into them; existing folders are left alone.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in [p for p in path.split("/") if p]:
            made = False
            if create:
                try:
                    os.mkdir(part, 0o2750, dir_fd=fd)
                    made = True
                except FileExistsError:
                    pass
            nfd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nfd
            if made and owner:
                try:
                    os.fchown(fd, *owner)
                except OSError:
                    pass  # exFAT: owners come from the mount options
        return fd
    except BaseException:
        os.close(fd)
        raise


def entries(files: list) -> list[dict]:
    """Manifest rows that are safe to act on."""
    good = []
    for f in files:
        name, path = str(f.get("file_name", "")), str(f.get("path", ""))
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if not SAFE_NAME.match(name) or os.path.basename(path) != name or not safe_dir(os.path.dirname(path)) or size <= 0:
            log(f"Library content: ignoring unsafe manifest entry {name!r}")
            continue
        digest = str(f.get("sha256") or "").lower()
        good.append({**f, "file_name": name, "path": path, "size": size, "sha256": digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""})
    return good


def zims(dir_fd: int) -> dict[str, int]:
    out = {}
    for name in os.listdir(dir_fd):
        if SAFE_NAME.match(name):
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if os.path.stat.S_ISREG(st.st_mode):
                out[name] = st.st_size
    return out


def copy_verified(src_dir: int, dest_dir: int, name: str, size: int, digest: str, owner: tuple[int, int] | None = None) -> bool:
    tmp = f".{name}.part"
    start = time.monotonic()
    src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_dir)
    try:
        if not os.path.stat.S_ISREG(os.fstat(src).st_mode):
            log(f"Library content: {name} isn't a regular file")
            return False
        dst = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640, dir_fd=dest_dir)
        h = hashlib.sha256()
        written = 0
        try:
            while True:
                block = os.read(src, CHUNK)
                if not block:
                    break
                os.write(dst, block)
                h.update(block)
                written += len(block)
            if owner:
                os.fchown(dst, *owner)
            os.fsync(dst)
        finally:
            os.close(dst)
    finally:
        os.close(src)
    if written != size or (digest and h.hexdigest() != digest):
        os.unlink(tmp, dir_fd=dest_dir)
        log(f"Library content: copy of {name} failed verification")
        return False
    os.replace(tmp, name, src_dir_fd=dest_dir, dst_dir_fd=dest_dir)
    log(f"Library content: {name} copied ({size // 1024**2} MB, {time.monotonic() - start:.0f}s)")
    return True


def backup(manifest_path: str, store: str) -> str:
    with open(manifest_path, encoding="utf-8") as fh:
        files = entries(json.load(fh).get("files", []))
    os.makedirs(store, exist_ok=True)
    store_fd = open_dir(store)
    try:
        wanted = {f["file_name"]: f for f in files}
        present = zims(store_fd)
        todo = [f for f in files if present.get(f["file_name"]) != f["size"]]
        stale = [n for n in present if n not in wanted]
        need = sum(f["size"] for f in todo) + GIB
        free = shutil.disk_usage(store).free
        if free < need and free + sum(present[n] for n in stale) >= need:
            # Room only once old editions go: prune first (they were removed
            # or replaced in the Library anyway).
            for name in stale:
                os.unlink(name, dir_fd=store_fd)
                log(f"Library content: pruned {name} early to make room")
            stale = []
            free = shutil.disk_usage(store).free
        if free < need:
            log(f"Library content skipped: needs {need / GIB:.1f} GB, {free / GIB:.1f} GB free")
            return f"skipped {sum(present.get(n, 0) for n in wanted)}"
        for f in todo:
            try:
                src_fd = open_dir(os.path.dirname(f["path"]))
            except OSError:
                log(f"Library content: {f['path']} not reachable, not copied")
                continue
            try:
                ok = copy_verified(src_fd, store_fd, f["file_name"], f["size"], f["sha256"])
            except FileNotFoundError:
                log(f"Library content: {f['path']} missing, not copied")
                continue
            finally:
                os.close(src_fd)
            if not ok:
                return f"failed {sum(zims(store_fd).values())}"
        for name in stale:
            os.unlink(name, dir_fd=store_fd)
            log(f"Library content: pruned {name}")
        for leftover in os.listdir(store_fd):
            if leftover.endswith(".part"):
                os.unlink(leftover, dir_fd=store_fd)
        now_present = zims(store_fd)
        kept = [f for f in files if now_present.get(f["file_name"]) == f["size"]]
        with open(os.path.join(store, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "files": kept}, fh, indent=1)
        return f"ok {sum(now_present.values())}"
    finally:
        os.close(store_fd)


def _drive_missing(target: str) -> bool:
    """A /mnt or /media target whose drive isn't mounted (path lands on /)."""
    if not target.startswith(("/mnt/", "/media/")):
        return False
    probe = target
    while not os.path.exists(probe) and probe != "/":
        probe = os.path.dirname(probe)
    return os.stat(probe).st_dev == os.stat("/").st_dev


def _ours_or_library_only(path: str, uid: int) -> bool:
    """True when ``path`` is missing, owned by the Library, or holds only ZIMs.

    Restore hands the folder to the Library user, so it must never take over
    someone else's folder that happens to sit under /mnt, /media or /srv.
    """
    try:
        fd = open_dir(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        if os.fstat(fd).st_uid == uid:
            return True
        for name in os.listdir(fd):
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not (name.endswith((".zim", ".zim.part")) and os.path.stat.S_ISREG(st.st_mode)):
                return False
        return True
    finally:
        os.close(fd)


def restore(store: str, manifest_path: str) -> str:
    """Put backed-up ZIMs back into the recorded content folder (or the microSD)."""
    try:
        with open(os.path.join(store, "manifest.json"), encoding="utf-8") as fh:
            files = entries(json.load(fh).get("files", []))
        with open(manifest_path, encoding="utf-8") as fh:
            target = str(json.load(fh).get("content_dir") or DEFAULT_TARGET).rstrip("/")
    except (OSError, ValueError, AttributeError):
        return f"failed 0 {DEFAULT_TARGET}"
    if not safe_dir(target):
        log(f"Library restore: content folder {target!r} not allowed — using the microSD")
        target = DEFAULT_TARGET
    elif _drive_missing(target):
        log(f"Library restore: drive for {target} isn't connected — using the microSD")
        target = DEFAULT_TARGET
    try:
        import pwd

        pw = pwd.getpwnam(APP_USER)
        owner = (pw.pw_uid, pw.pw_gid)
    except (ImportError, KeyError):
        owner = None
    if owner and not _ours_or_library_only(target, owner[0]):
        log(f"Library restore: {target} already holds other files — using the microSD")
        target = DEFAULT_TARGET
    total = 0
    store_fd = open_dir(store)
    dest_fd = open_dir(target, create=True, owner=owner)
    try:
        if owner:
            try:
                os.fchown(dest_fd, *owner)
                os.fchmod(dest_fd, 0o2750)
            except OSError:
                pass
        have = zims(dest_fd)
        for f in files:
            name, size = f["file_name"], f["size"]
            if have.get(name) == size:
                total += size
                continue
            if shutil.disk_usage(target).free < size + GIB:
                log(f"Library restore: no room for {name} in {target}")
                continue
            try:
                if copy_verified(store_fd, dest_fd, name, size, f["sha256"], owner):
                    total += size
            except FileNotFoundError:
                log(f"Library restore: {name} not in the backup store")
    finally:
        os.close(store_fd)
        os.close(dest_fd)
    return f"ok {total} {target}"


def main(argv: list[str]) -> int:
    global _log_path
    if len(argv) < 3 or argv[0] not in {"backup", "restore"}:
        print(__doc__, file=sys.stderr)
        return 2
    _log_path = argv[3] if len(argv) > 3 else ""
    try:
        result = backup(argv[1], argv[2]) if argv[0] == "backup" else restore(argv[1], argv[2])
    except Exception as exc:  # report, never crash the platform backup/restore
        log(f"Library content {argv[0]} error: {exc}")
        result = "failed 0" if argv[0] == "backup" else f"failed 0 {DEFAULT_TARGET}"
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
