"""StonePi Recover."""

import hashlib
from pathlib import Path

__version__ = "0.2.0"


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
# Shared /assets/fonts/ and /assets/brand/ (nginx alias on the Pi; Recover also
# serves them itself for direct :8099 use when nginx is down).
BRAND_ROOT = next(
    (
        path
        for path in (
            _APP_DIR.parents[2] / "packages" / "stonepi_brand",
            Path("/opt/stonepi/packages/stonepi_brand"),
        )
        if (path / "fonts").is_dir()
    ),
    None,
)
__fonts_rev__ = _asset_rev(BRAND_ROOT / "fonts", BRAND_ROOT / "static") if BRAND_ROOT else "0"
