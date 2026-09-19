from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

MAX_ZIP_FILES = 2500
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
SKIP_PREFIXES = ("__macosx/",)
SKIP_NAMES = {".ds_store", "thumbs.db"}

FILE_MAP_RE = re.compile(r"```(?:json|filemap)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def safe_rel(path: str) -> str | None:
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    if not raw or raw.startswith("../") or "/../" in f"/{raw}/":
        return None
    parts = PurePosixPath(raw).parts
    if any(p in ("..", "") for p in parts):
        return None
    return PurePosixPath(*parts).as_posix()


def list_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.append(path.relative_to(root).as_posix())
    return out


def parse_file_map(text: str) -> dict[str, str]:
    body = (text or "").strip()
    if not body:
        return {}
    candidates: list[str] = []
    for match in FILE_MAP_RE.finditer(body):
        candidates.append(match.group(1))
    if not candidates:
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            candidates.append(body[start : end + 1])
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        files = parsed.get("files") if "files" in parsed else parsed
        if not isinstance(files, dict):
            continue
        clean: dict[str, str] = {}
        for key, value in files.items():
            rel = safe_rel(str(key))
            if rel is None:
                continue
            clean[rel] = str(value)
        if clean:
            return clean
    return {}


def apply_file_map(root: Path, mapping: dict[str, str]) -> list[str]:
    applied: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in mapping.items():
        safe = safe_rel(rel)
        if safe is None:
            continue
        dest = root / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        applied.append(safe)
    return applied


def workspace_zip(root: Path) -> bytes:
    if not (root / "index.html").is_file():
        raise ValueError("Add index.html before publishing.")
    total = 0
    count = 0
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            lower = rel.lower()
            if any(lower.startswith(p) for p in SKIP_PREFIXES):
                continue
            if path.name.lower() in SKIP_NAMES:
                continue
            data = path.read_bytes()
            total += len(data)
            count += 1
            if count > MAX_ZIP_FILES:
                raise ValueError("Too many files for FileServe (max 2500).")
            if total > MAX_ZIP_UNCOMPRESSED:
                raise ValueError("Site is too large for FileServe (max 200 MB uncompressed).")
            archive.writestr(rel, data)
    return buffer.getvalue()
