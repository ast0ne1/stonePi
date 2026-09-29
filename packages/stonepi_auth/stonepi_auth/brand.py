"""Serve the shared StonePi fonts (``/assets/fonts/``) from an app.

On the Pi nginx serves ``/assets/fonts/`` straight from
``packages/stonepi_brand/fonts`` before requests reach any app, so these
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

FONTS_URL = "/assets/fonts"
# packages/stonepi_auth/stonepi_auth/brand.py -> packages/stonepi_brand/fonts
BRAND_FONTS_DIR = Path(__file__).resolve().parents[2] / "stonepi_brand" / "fonts"


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


def _fonts_dirs() -> list[Path]:
    # Pip-installed copies of this package live in a venv, so also look in the
    # monorepo / appliance tree that nginx serves /assets/fonts/ from.
    candidates = [BRAND_FONTS_DIR]
    root = os.environ.get("STONEPI_ROOT", "").strip()
    if root:
        candidates.append(Path(root) / "packages" / "stonepi_brand" / "fonts")
    candidates.append(Path("/opt/stonepi/packages/stonepi_brand/fonts"))
    return candidates


def fonts_rev() -> str:
    """Cache-bust token for the shared ``/assets/fonts/fonts.css``."""
    for candidate in _fonts_dirs():
        if candidate.is_dir():
            return asset_rev(candidate)
    return "0"


def mount_brand_fonts(app: Any) -> None:
    """Starlette / FastAPI: mount the fonts folder if it exists."""
    if not BRAND_FONTS_DIR.is_dir():
        return
    from starlette.staticfiles import StaticFiles

    app.mount(FONTS_URL, StaticFiles(directory=str(BRAND_FONTS_DIR)), name="stonepi_brand_fonts")


def add_brand_fonts_route(app: Any) -> None:
    """Flask: add a route serving the fonts folder if it exists."""
    if not BRAND_FONTS_DIR.is_dir():
        return
    from flask import send_from_directory

    def stonepi_brand_font(filename: str):
        return send_from_directory(str(BRAND_FONTS_DIR), filename, max_age=7 * 24 * 3600)

    app.add_url_rule(f"{FONTS_URL}/<path:filename>", "stonepi_brand_font", stonepi_brand_font)
