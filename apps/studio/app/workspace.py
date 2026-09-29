from __future__ import annotations

import html
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

# Preferred build output: raw file bodies between tags, so the model never has to
# JSON-escape a whole game (the old filemap broke on one stray quote or newline).
FILE_OPEN_RE = re.compile(r"""<studio-file\s+path=["']([^"'>]+)["']\s*>""", re.IGNORECASE)
FILE_CLOSE = "</studio-file>"
FILE_BLOCK_RE = re.compile(
    r"""<studio-file\s+path=["']([^"'>]+)["']\s*>\n?(.*?)</studio-file>""", re.DOTALL | re.IGNORECASE
)
_FENCE_WRAP_RE = re.compile(r"^\s*```[\w-]*\n(.*?)\n```\s*$", re.DOTALL)
_LOCAL_REF_RE = re.compile(
    r"""<(?:script|link|img|audio|source)\b[^>]*?\b(?:src|href)=["']([^"'#?]+)""", re.IGNORECASE
)
TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md"}


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


def _unwrap_fence(body: str) -> str:
    match = _FENCE_WRAP_RE.match(body)
    return match.group(1) if match else body


def parse_file_blocks(text: str) -> tuple[dict[str, str], list[str]]:
    """Return (complete files, paths whose block never closed — i.e. truncated)."""
    body = text or ""
    files: dict[str, str] = {}
    for match in FILE_BLOCK_RE.finditer(body):
        rel = safe_rel(match.group(1))
        if rel is None:
            continue
        files[rel] = _unwrap_fence(match.group(2)).rstrip() + "\n"
    opened = FILE_OPEN_RE.findall(body)
    unclosed = []
    if len(opened) > body.lower().count(FILE_CLOSE):
        last = safe_rel(opened[-1])
        if last:
            unclosed.append(last)
    return files, unclosed


def strip_file_blocks(text: str) -> str:
    """Prose only: drop complete blocks and anything after an unclosed opener."""
    body = FILE_BLOCK_RE.sub("", text or "")
    opener = FILE_OPEN_RE.search(body)
    if opener:
        body = body[: opener.start()]
    return FILE_MAP_RE.sub("", body).strip()


def read_text_files(root: Path, *, limit: int = 180_000) -> dict[str, str]:
    """Current text sources, so rebuilds edit the real code instead of guessing it."""
    out: dict[str, str] = {}
    used = 0
    for rel in list_files(root):
        path = root / rel
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if used + len(text) > limit:
            break
        out[rel] = text
        used += len(text)
    return out


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def page_title(root: Path) -> str:
    index = root / "index.html"
    if not index.is_file():
        return ""
    match = _TITLE_RE.search(index.read_text(encoding="utf-8", errors="replace"))
    return html.unescape(match.group(1)).strip() if match else ""


_JS_LOOKUP_RE = re.compile(
    r"""(getElementById|querySelector(?:All)?)\(\s*(["'])([^"'`]+)\2\s*\)"""
)
_SIMPLE_SELECTOR_RE = re.compile(r"^(?:#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=[^\]]*)?\])$")


def _html_has(html: str, kind: str, name: str) -> bool:
    if kind == "id":
        return re.search(rf"""\bid=["']{re.escape(name)}["']""", html) is not None
    if kind == "class":
        return re.search(rf"""\bclass=["'][^"']*(?<![\w-]){re.escape(name)}(?![\w-])""", html) is not None
    return re.search(rf"""<[^>]*\s{re.escape(name)}(?:[\s=>/])""", html) is not None


def _js_creates(js: str, name: str) -> bool:
    rest = _JS_LOOKUP_RE.sub("", js)
    n = re.escape(name)
    return (
        re.search(rf"""["'`]{n}["'`]""", rest) is not None
        or re.search(rf"""(?:class|id)=\\?["'][^"']*(?<![\w-]){n}(?![\w-])""", rest) is not None
        or re.search(rf"""<[^>]*\s{n}[\s=>/]""", rest) is not None
    )


def missing_lookups(html: str, js: str) -> list[str]:
    """Element lookups in JS that nothing in the HTML (or the JS itself) can satisfy.

    The classic broken build: the JS grabs `#score` or `[data-timer]` that the HTML never
    defines, gets null, and throws on first use, often every frame.
    Only document-level lookups with a single simple selector are checked, and names the
    JS mentions again elsewhere (e.g. elements it creates) are skipped to avoid false alarms.
    """
    missing: list[str] = []
    for match in _JS_LOOKUP_RE.finditer(js):
        start = match.start()
        if not js[max(0, start - 9) : start].endswith("document."):
            continue  # e.g. el.querySelector(...) depends on runtime structure
        fn, selector = match.group(1), match.group(3).strip()
        if fn == "getElementById":
            kind, name = "id", selector
        else:
            simple = _SIMPLE_SELECTOR_RE.match(selector)
            if not simple:
                continue
            kind, name = ("id", simple.group(1)) if simple.group(1) else (
                ("class", simple.group(2)) if simple.group(2) else ("attr", simple.group(3))
            )
        if _html_has(html, kind, name):
            continue
        if _js_creates(js, name):
            continue  # the JS builds that element itself (createElement, innerHTML, classList.add…)
        label = f"#{name}" if kind == "id" else selector
        if label not in missing:
            missing.append(label)
    return missing


def check_site(root: Path) -> list[str]:
    """Static checks that catch the usual reasons a preview comes up blank."""
    issues: list[str] = []
    index = root / "index.html"
    if not index.is_file():
        return ["index.html is missing, so there is nothing to preview."]
    html = index.read_text(encoding="utf-8", errors="replace")
    if "<body" not in html.lower():
        issues.append("index.html has no <body>; it may have been cut off.")
    for ref in _LOCAL_REF_RE.findall(html):
        ref = ref.strip()
        if not ref or ref.startswith(("http:", "https:", "data:", "blob:", "mailto:", "//")):
            continue
        if ref.startswith("/"):
            issues.append(f"{ref} uses an absolute path; it must be relative (drop the leading /).")
            continue
        rel = safe_rel(ref)
        if rel and not (root / rel).is_file():
            issues.append(f"index.html loads {ref}, but that file does not exist.")
    for rel in list_files(root):
        if rel.endswith(".js"):
            js = (root / rel).read_text(encoding="utf-8", errors="replace")
            for label in missing_lookups(html, js):
                issues.append(
                    f"{rel} looks for {label}, but index.html has no element like that, "
                    "so that part of the code will crash."
                )
    for rel in list_files(root):
        if rel.endswith((".js", ".css")):
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
            if text.count("{") != text.count("}"):
                issues.append(f"{rel} has unbalanced braces; it may be truncated.")
    return issues


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
