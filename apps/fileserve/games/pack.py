"""Zip a games/<name>/ folder for FileServe Site upload."""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DIST = REPO / "dist"


def pack_game(name: str) -> Path:
    folder = ROOT / name
    if not folder.is_dir():
        raise SystemExit(f"No game folder at games/{name}/")
    index = folder / "index.html"
    if not index.is_file():
        raise SystemExit(f"games/{name}/ needs index.html at the folder root.")
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"{name}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            archive.write(path, path.relative_to(folder).as_posix())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack a games/<name> folder into dist/<name>.zip")
    parser.add_argument("name", nargs="?", help="Game folder name under games/")
    parser.add_argument("--all", action="store_true", help="Pack every game folder")
    args = parser.parse_args()
    if args.all:
        names = sorted(
            p.name
            for p in ROOT.iterdir()
            if p.is_dir() and (p / "index.html").is_file()
        )
        if not names:
            raise SystemExit("No game folders with index.html found under games/.")
        for name in names:
            out = pack_game(name)
            print(f"Wrote {out.relative_to(REPO)} ({out.stat().st_size} bytes)")
        return
    if not args.name:
        parser.print_help()
        raise SystemExit(2)
    out = pack_game(args.name.strip())
    print(f"Wrote {out.relative_to(REPO)} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
