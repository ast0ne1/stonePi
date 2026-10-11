"""Render PNG favicon / touch-icon / PWA sizes from the app and system icon SVGs.

    python packages/stonepi_brand/render_icons.py [--force]

Windows dev machine only (uses headless Edge or Chrome); the PNGs are committed.
Writes ``static/icons/png/<id>-<size>.png`` for 32 (favicon), 180 (apple-touch,
full-bleed on the tile ground since iOS rounds it itself), 192 and 512 (manifest).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "static" / "icons"
OUT = ICONS / "png"
SIZES = ((32, False), (180, True), (192, False), (512, False))
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)


def _browser() -> str:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    raise SystemExit("No Edge or Chrome found")


def _shot(browser: str, html: Path, target: Path, size: int, profile: Path) -> None:
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--default-background-color=00000000",
        f"--user-data-dir={profile}",
        f"--window-size={size},{size}",
        f"--screenshot={target}",
        html.as_uri(),
    ]
    for _ in range(3):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass  # Headless sometimes lingers after writing the file.
        if target.exists() and target.stat().st_size:
            return
    raise SystemExit(f"Could not render {target.name}")


def main(argv: list[str]) -> int:
    force = "--force" in argv
    browser = _browser()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        for svg in sorted((ICONS / "apps").glob("*.svg")) + sorted((ICONS / "system").glob("*.svg")):
            ground = re.search(r'rx="22" fill="(#[0-9A-Fa-f]{6})"', svg.read_text(encoding="utf-8")).group(1)
            for size, full_bleed in SIZES:
                target = OUT / f"{svg.stem}-{size}.png"
                if target.exists() and not force:
                    continue
                target.unlink(missing_ok=True)
                html = tmp / f"{svg.stem}-{size}.html"
                bg = ground if full_bleed else "transparent"
                html.write_text(
                    f'<!doctype html><body style="margin:0;background:{bg}">'
                    f'<img src="{svg.as_uri()}" style="display:block;width:{size}px;height:{size}px"></body>',
                    encoding="utf-8",
                )
                _shot(browser, html, target, size, tmp / "profile")
            print("rendered", svg.stem)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
