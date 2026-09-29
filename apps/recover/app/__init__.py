"""StonePi Recover."""

import hashlib
from pathlib import Path

__version__ = "0.1.8"


def _asset_rev(*dirs: Path) -> str:
    # Local copy of stonepi_auth.brand.asset_rev — Recover must start even when StonePi packages are broken.
    digest = hashlib.sha1()
    for base in dirs:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                stat = path.stat()
                digest.update(f"{path.relative_to(base).as_posix()}:{stat.st_size}:{stat.st_mtime_ns};".encode())
    return digest.hexdigest()[:10]


_APP_DIR = Path(__file__).resolve().parent
__asset_rev__ = _asset_rev(_APP_DIR / "static")  # cache-bust token; changes with static/
# Shared /assets/fonts/ (nginx alias on the Pi; monorepo path in run-dev).
__fonts_rev__ = _asset_rev(
    *[
        path
        for path in (
            _APP_DIR.parents[2] / "packages" / "stonepi_brand" / "fonts",
            Path("/opt/stonepi/packages/stonepi_brand/fonts"),
        )
        if path.is_dir()
    ][:1]
)
