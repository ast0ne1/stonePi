#!/usr/bin/env python3
"""Build per-app (and optional platform) zip assets from the StonePi monorepo.

Usage:
  python scripts/build_release_zips.py
  python scripts/build_release_zips.py --out dist/releases --apps newscast,fileserve
  python scripts/build_release_zips.py --all

App assets are named ``stonepi-{app_id}-{version}.zip`` (StonePi portal variants —
not standalone upstream apps). Platform packs use ``stonepi-platform-{version}.zip``.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")

APP_IDS = ("auth", "dashboard", "newscast", "fileserve", "eventtrakr", "pinboard", "studio", "pricescout", "sportguide")
APP_INCLUDE = (
    "app",
    "requirements.txt",
    "README.md",
    "INSTALL.md",
    "CHANGELOG.md",
    ".env.example",
    "run.py",
    "run-local.bat",
    "deploy",
)
PLATFORM_INCLUDE = ("VERSION", "deploy", "packages", "scripts", "README.md", "CHANGELOG.md")
PLATFORM_EXCLUDE_PREFIXES = (
    "deploy/packages/",
)
# Local-only Pi overlay helpers — never ship in stonepi-platform-*.zip
PLATFORM_SCRIPT_EXCLUDE_GLOBS = (
    "scripts/push-*-fixes.*",
    "scripts/apply-*-on-pi.sh",
    "scripts/*-ux-overlay*",
)

STONEPI_NOTICE = """\
StonePi portal variant
======================

This zip is built from the StonePi monorepo for use with StonePi
(Dashboard Updates / stonepi_update overlay into /opt/stonepi/apps/<app>).

It is not a standalone NewsCast / FileServe / EventTrakr / etc. release.
Install StonePi via deploy/install.sh (or overlay this zip on an existing Pi).
"""


def read_app_version(app_dir: Path) -> str:
    init_py = app_dir / "app" / "__init__.py"
    if not init_py.exists():
        raise SystemExit(f"Missing {init_py}")
    match = VERSION_RE.search(init_py.read_text(encoding="utf-8", errors="replace"))
    if not match:
        raise SystemExit(f"No __version__ in {init_py}")
    return match.group(1)


def read_platform_version() -> str:
    path = ROOT / "VERSION"
    if not path.exists():
        raise SystemExit("Missing root VERSION")
    return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()


def should_skip_platform_path(full_arc: str) -> bool:
    return any(fnmatch.fnmatch(full_arc, pattern) for pattern in PLATFORM_SCRIPT_EXCLUDE_GLOBS)


def add_path(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: str,
    *,
    skip_prefixes: tuple[str, ...] = (),
    skip_script_helpers: bool = False,
) -> None:
    if source.is_dir():
        for path in source.rglob("*"):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if ".venv" in path.parts or path.name == ".env":
                continue
            rel_posix = path.relative_to(source).as_posix()
            full_arc = f"{arcname}/{rel_posix}" if arcname else rel_posix
            if any(full_arc == p.rstrip("/") or full_arc.startswith(p) for p in skip_prefixes):
                continue
            if skip_script_helpers and should_skip_platform_path(full_arc):
                continue
            if "data" in path.parts and "app" not in path.parts[: path.parts.index("data")]:
                # Skip runtime data/ under app root, keep app/data bundled assets
                rel = path.relative_to(source)
                if rel.parts and rel.parts[0] == "data":
                    continue
            archive.write(path, full_arc)
    else:
        archive.write(source, arcname)


def build_app_zip(app_id: str, out_dir: Path) -> Path:
    app_dir = ROOT / "apps" / app_id
    if not app_dir.is_dir():
        raise SystemExit(f"Missing app directory {app_dir}")
    version = read_app_version(app_dir)
    dest = out_dir / f"stonepi-{app_id}-{version}.zip"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("STONEPI.txt", STONEPI_NOTICE)
        for name in APP_INCLUDE:
            source = app_dir / name
            if not source.exists():
                continue
            add_path(archive, source, name)
    return dest


def build_platform_zip(out_dir: Path) -> Path:
    version = read_platform_version()
    dest = out_dir / f"stonepi-platform-{version}.zip"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("STONEPI.txt", STONEPI_NOTICE)
        for name in PLATFORM_INCLUDE:
            source = ROOT / name
            if not source.exists():
                continue
            skip = PLATFORM_EXCLUDE_PREFIXES if name == "deploy" else ()
            add_path(
                archive,
                source,
                name,
                skip_prefixes=skip,
                skip_script_helpers=(name == "scripts"),
            )
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "releases")
    parser.add_argument("--apps", default=",".join(APP_IDS), help="Comma-separated app ids")
    parser.add_argument("--platform", action="store_true", help="Also build stonepi-platform-*.zip")
    parser.add_argument("--all", action="store_true", help="Build every app zip and the platform pack")
    args = parser.parse_args()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    apps = [item.strip() for item in args.apps.split(",") if item.strip()]
    if args.all:
        apps = list(APP_IDS)
    built = []
    for app_id in apps:
        if app_id not in APP_IDS:
            raise SystemExit(f"Unknown app id: {app_id}")
        path = build_app_zip(app_id, out_dir)
        built.append(path)
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"wrote {shown}")
    if args.platform or args.all:
        path = build_platform_zip(out_dir)
        built.append(path)
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"wrote {shown}")
    print(f"{len(built)} package(s) in {out_dir}")


if __name__ == "__main__":
    main()
