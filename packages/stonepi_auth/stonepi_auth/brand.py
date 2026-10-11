"""Serve the shared StonePi look (``/assets/fonts/``, ``/assets/brand/``) from an app.

``packages/stonepi_brand`` holds the design system: fonts, ``tokens.css``,
``components.css``, logos and app / system icons. On the Pi nginx serves both
URL prefixes straight from that package before requests reach any app, so these
routes only answer when an app is reached directly (Windows ``run-dev``).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any

# Windows' registry often lacks these, which serves fonts as text/plain.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")

FONTS_URL = "/assets/fonts"
BRAND_URL = "/assets/brand"
# packages/stonepi_auth/stonepi_auth/brand.py -> packages/stonepi_brand/{fonts,static}
BRAND_ROOT = Path(__file__).resolve().parents[2] / "stonepi_brand"
BRAND_FONTS_DIR = BRAND_ROOT / "fonts"
BRAND_STATIC_DIR = BRAND_ROOT / "static"


def asset_rev(*dirs: Path | str) -> str:
    """Cache-bust token for ``?v=``: a short hash of file names, sizes and mtimes.

    Computed once at startup, so any CSS/JS/font change (edit or update overlay)
    gets a new token on the next restart — nothing to bump by hand.
    """
    digest = hashlib.sha1()
    for root in dirs:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            stat = path.stat()
            digest.update(f"{path.relative_to(base).as_posix()}:{stat.st_size}:{stat.st_mtime_ns};".encode())
    return digest.hexdigest()[:10]


def _brand_roots() -> list[Path]:
    # Pip-installed copies of this package live in a venv, so also look in the
    # monorepo / appliance tree that nginx serves /assets/ from.
    candidates = [BRAND_ROOT]
    root = os.environ.get("STONEPI_ROOT", "").strip()
    if root:
        candidates.append(Path(root) / "packages" / "stonepi_brand")
    candidates.append(Path("/opt/stonepi/packages/stonepi_brand"))
    return candidates


def fonts_rev() -> str:
    """Cache-bust token for everything under ``/assets/`` (fonts and brand files)."""
    for candidate in _brand_roots():
        if (candidate / "fonts").is_dir():
            return asset_rev(candidate / "fonts", candidate / "static")
    return "0"


# Same token; the name reads better next to /assets/brand/ links.
brand_rev = fonts_rev


def mount_brand_fonts(app: Any) -> None:
    """Starlette / FastAPI: mount the fonts and brand folders if they exist."""
    from starlette.staticfiles import StaticFiles

    if BRAND_FONTS_DIR.is_dir():
        app.mount(FONTS_URL, StaticFiles(directory=str(BRAND_FONTS_DIR)), name="stonepi_brand_fonts")
    if BRAND_STATIC_DIR.is_dir():
        app.mount(BRAND_URL, StaticFiles(directory=str(BRAND_STATIC_DIR)), name="stonepi_brand_static")


def add_brand_fonts_route(app: Any) -> None:
    """Flask: add routes serving the fonts and brand folders if they exist."""
    from flask import send_from_directory

    if BRAND_FONTS_DIR.is_dir():

        def stonepi_brand_font(filename: str):
            return send_from_directory(str(BRAND_FONTS_DIR), filename, max_age=7 * 24 * 3600)

        app.add_url_rule(f"{FONTS_URL}/<path:filename>", "stonepi_brand_font", stonepi_brand_font)
    if BRAND_STATIC_DIR.is_dir():

        def stonepi_brand_static(filename: str):
            return send_from_directory(str(BRAND_STATIC_DIR), filename, max_age=7 * 24 * 3600)

        app.add_url_rule(f"{BRAND_URL}/<path:filename>", "stonepi_brand_static", stonepi_brand_static)


SYSTEM_ICON_IDS = frozenset({"dashboard", "auth", "notify", "recover", "carthing", "trmnl"})


def brand_color(name: str, theme: str = "dark") -> str:
    """A colour token's value from the design system's tokens.json (e.g. ``canvas``)."""
    import json

    for candidate in _brand_roots():
        path = candidate / "tokens.json"
        if path.is_file():
            try:
                tokens = json.loads(path.read_text(encoding="utf-8"))["color"]["tokens"]
            except (OSError, ValueError, KeyError):
                break
            for tok in tokens:
                if tok.get("name") == name:
                    return str(tok["value"].get(theme) or "")
            break
    return ""


def manifest_icons(icon: str) -> list[dict[str, str]]:
    """PWA manifest ``icons`` for an app or system icon id (PNG sizes plus the SVG)."""
    group = "system" if icon in SYSTEM_ICON_IDS else "apps"
    return [
        {"src": f"{BRAND_URL}/icons/png/{icon}-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": f"{BRAND_URL}/icons/png/{icon}-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": f"{BRAND_URL}/icons/{group}/{icon}.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ]
