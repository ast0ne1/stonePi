"""Web pages the displays can show (F14): defined once in Notify, picked by a TRMNL
"Web page" Display, a Car Thing web widget or the Car Thing web screensaver.

Stored as ``web_pages.json`` next to ``displays.json``. Admin-only, so the URL is
only checked for an http(s) scheme and a host.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import displays as _displays

_lock = threading.RLock()
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_PAGES = 20
MAX_CSS = 4000

# CSS presets added before the page's own CSS. "flat" suits dashboards with a photo or
# blurred background (Homepage): no background images, blur or shadows, so what's left is
# flat colour that thresholds cleanly on e-ink (and inverts cleanly when the page is dark).
CSS_PRESETS: dict[str, dict[str, str]] = {
    "none": {"label": "None", "css": ""},
    "flat": {
        "label": "Flat: no background image, blur or shadows",
        "css": (
            "*,*::before,*::after{background-image:none!important;backdrop-filter:none!important;"
            "-webkit-backdrop-filter:none!important;box-shadow:none!important;text-shadow:none!important;"
            "transition:none!important;animation:none!important}"
        ),
    },
}
DEFAULT_PRESET = "flat"

PAGE_DEFAULTS: dict[str, Any] = {
    "id": "",
    "name": "",
    "url": "",
    "preset": DEFAULT_PRESET,
    "hide": "",  # CSS selectors, one per line, hidden before the capture
    "css": "",
    "settle_s": 3,
    "scale": 1.0,
}


def _path() -> Path:
    data_dir = _displays._data_dir
    if data_dir is None:
        raise RuntimeError("stonepi_display displays not configured")
    return Path(data_dir) / "web_pages.json"


def clean_page_id(value: Any) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")[:40]
    return text if _ID.match(text or "") else ""


def validate_page_url(raw: Any) -> str:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use a full http:// or https:// address.")
    if parsed.username or parsed.password:
        raise ValueError("Leave sign-in details out of the address.")
    return value


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, number))


def normalize_page(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    page_id = clean_page_id(raw.get("id"))
    if not page_id:
        return None
    try:
        url = validate_page_url(raw.get("url"))
    except ValueError:
        url = ""
    preset = str(raw.get("preset") or DEFAULT_PRESET)
    hide = "\n".join(line.strip() for line in str(raw.get("hide") or "").splitlines() if line.strip())
    return {
        "id": page_id,
        "name": (str(raw.get("name") or "").strip() or page_id)[:60],
        "url": url,
        "preset": preset if preset in CSS_PRESETS else DEFAULT_PRESET,
        "hide": hide[:MAX_CSS],
        "css": str(raw.get("css") or "")[:MAX_CSS],
        "settle_s": int(_number(raw.get("settle_s"), PAGE_DEFAULTS["settle_s"], 0, 15)),
        "scale": round(_number(raw.get("scale"), PAGE_DEFAULTS["scale"], 0.25, 3.0), 2),
    }


def page_css(page: dict[str, Any]) -> str:
    """Preset + hidden selectors + the page's own CSS, as one style block."""
    parts = [CSS_PRESETS.get(page.get("preset") or "", CSS_PRESETS["none"])["css"]]
    selectors = [s for s in str(page.get("hide") or "").splitlines() if s.strip() and "{" not in s and "}" not in s]
    # One rule per selector: a typo in one mustn't stop the others hiding.
    parts.extend(f"{s.strip()}{{display:none!important}}" for s in selectors)
    parts.append(str(page.get("css") or ""))
    return "\n".join(p for p in parts if p)


def load_pages() -> list[dict[str, Any]]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("pages") if isinstance(data, dict) else data
    pages: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        page = normalize_page(item)
        if page and not any(p["id"] == page["id"] for p in pages):
            pages.append(page)
    return pages[:MAX_PAGES]


def _save(pages: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pages": pages}, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_page(page_id: str) -> dict[str, Any] | None:
    target = clean_page_id(page_id)
    return next((p for p in load_pages() if p["id"] == target), None)


def new_page_id(name: str) -> str:
    base = clean_page_id(name) or "page"
    taken = {p["id"] for p in load_pages()}
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base[:36]}-{n}"
        n += 1
    return candidate


def upsert_page(raw: dict[str, Any]) -> dict[str, Any]:
    """Add or replace one page. Raises ValueError for a bad URL or a full list."""
    validate_page_url(raw.get("url"))
    page = normalize_page(raw)
    if page is None:
        raise ValueError("That page needs a name.")
    with _lock:
        pages = load_pages()
        for i, item in enumerate(pages):
            if item["id"] == page["id"]:
                pages[i] = page
                break
        else:
            if len(pages) >= MAX_PAGES:
                raise ValueError(f"Up to {MAX_PAGES} web pages.")
            pages.append(page)
        _save(pages)
    return page


def create_page(raw: dict[str, Any]) -> dict[str, Any]:
    """Add a new page with an id made from its name. The id is picked under the lock, so
    two pages added at once with the same name can't overwrite each other."""
    with _lock:
        return upsert_page({**raw, "id": new_page_id(str(raw.get("name") or ""))})


def delete_page(page_id: str) -> bool:
    target = clean_page_id(page_id)
    with _lock:
        pages = load_pages()
        kept = [p for p in pages if p["id"] != target]
        if len(kept) == len(pages):
            return False
        _save(kept)
    return True
