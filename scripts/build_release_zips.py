#!/usr/bin/env python3
"""Build per-app (and optional platform) zip assets from the StonePi monorepo.

Usage:
  python scripts/build_release_zips.py
  python scripts/build_release_zips.py --out dist/releases --apps newscast,fileserve
  python scripts/build_release_zips.py --all
  python scripts/build_release_zips.py --versions-table
  python scripts/build_release_zips.py --all --dry-run [--verbose]

Before building, a pre-flight check runs. In a git checkout it refuses when apps/, packages/ or
deploy/ have untracked, non-ignored files (they would be missing from the ``git archive`` tag
tarball the web installer downloads; override with ``--allow-dirty``). In every tree (checkout or
LF tag export) it checks that each ``$DEST/...`` path deploy/install.sh references exists, and,
in a checkout, that it is tracked and present in tag ``v<VERSION>`` when that tag exists.

Runtime state, caches and secrets never ship (see ``is_denied``). Text files (.sh/.py/systemd/
udev/nginx/nftables and any other UTF-8 text) are written with LF line endings whatever the
checkout's autocrlf setting; .bat/.cmd/.ps1 are left as they are.

App ids come from APP_CATALOG (packages/stonepi_auth/stonepi_auth/catalog.py). Apps marked
``ships_with: platform`` (the system apps: Dashboard, Auth, Notify, Recover) have no zip of
their own; they ship inside the platform pack.

App assets are named ``stonepi-{app_id}-{version}.zip`` (StonePi portal variants —
not standalone upstream apps). Platform packs use ``stonepi-platform-{version}.zip``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"__version__\s*=\s*['\"]([^'\"]+)['\"]")



def _load_catalog() -> list[dict]:
    # Load catalog.py on its own: it has no imports, while the stonepi_auth package needs its deps.
    path = ROOT / "packages" / "stonepi_auth" / "stonepi_auth" / "catalog.py"
    spec = importlib.util.spec_from_file_location("_stonepi_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.APP_CATALOG


def _load_service_apps() -> tuple[str, ...]:
    path = ROOT / "packages" / "stonepi_auth" / "stonepi_auth" / "catalog.py"
    spec = importlib.util.spec_from_file_location("_stonepi_catalog_services", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(getattr(module, "PLATFORM_SERVICE_APP_IDS", ()))


APP_CATALOG = _load_catalog()
APP_IDS = tuple(item["id"] for item in APP_CATALOG if not item.get("ships_with"))
PLATFORM_APP_IDS = tuple(item["id"] for item in APP_CATALOG if item.get("ships_with") == "platform")
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
# Plus optional platform services (apps/carthing) that ship with the platform but aren't catalog apps.
PLATFORM_APP_IDS = PLATFORM_APP_IDS + _load_service_apps()
PLATFORM_INCLUDE = ("VERSION", "deploy", "packages", "scripts", "README.md", "CHANGELOG.md", *(f"apps/{app_id}" for app_id in PLATFORM_APP_IDS))
PLATFORM_EXCLUDE_PREFIXES = (
    "deploy/packages/",
)
# Local-only Pi overlay helpers — never ship in stonepi-platform-*.zip
PLATFORM_SCRIPT_EXCLUDE_GLOBS = (
    "scripts/push-*-fixes.*",
    "scripts/apply-*-on-pi.sh",
    "scripts/*-ux-overlay*",
    "scripts/*-bootstrap-on-pi.sh",
    "scripts/cleanup-pi-tmp.*",
)

# Never ship these, wherever they sit in the tree (runtime state, caches, secrets, scratch).
DENY_DIR_NAMES = frozenset({
    "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".git",
})
DENY_DIR_GLOBS = ("*.egg-info",)
DENY_FILE_GLOBS = (
    "*.pyc", "*.pyo",
    ".env", ".env.*",           # .env.example is allowed below
    "*.db", "*.db-*", "*.db.*", "*.sqlite", "*.sqlite-*", "*.sqlite.*", "*.sqlite3",
    "*.key", "*.pem", "*.crt",  # nothing tracked ships one; apps/*/data/tls has runtime certs
    "session.secret", "vault.key", "secrets.enc",
    "*.log",
    "*.tar", "*.tar.*", "*.tgz",
)
ALLOW_FILE_NAMES = frozenset({".env.example"})
# Written into zips with LF line endings (a CRLF shell script fails on the Pi).
LF_SUFFIXES = frozenset({
    ".sh", ".py", ".service", ".target", ".timer", ".rules", ".conf", ".nft",
})
LF_NAMES = frozenset({".env.example"})
# Windows-only helpers keep CRLF; everything else that is text (no NUL byte, valid UTF-8) also
# goes LF, matching .gitattributes (e.g. deploy/apt/51stonepi-unattended, *.toml, *.html).
CRLF_SUFFIXES = frozenset({".bat", ".cmd", ".ps1"})


def looks_like_text(data: bytes) -> bool:
    if b"\0" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def is_denied(full_arc: str) -> bool:
    """True when an archive path is runtime state, a cache, a secret or scratch."""
    parts = full_arc.split("/")
    dirs, name = parts[:-1], parts[-1]
    for index, part in enumerate(dirs):
        if part in DENY_DIR_NAMES or any(fnmatch.fnmatch(part, g) for g in DENY_DIR_GLOBS):
            return True
        # packages/<pkg>/build/ (setuptools output)
        if part == "build" and parts[0] == "packages":
            return True
    if "data" in dirs:
        # Only bundled app/data/ assets ship (apps/<id>/app/data/ or app/data/ in an app zip);
        # apps/<id>/data/, the root data/ and any other data/ are runtime state.
        first = dirs.index("data")
        if first == 0 or dirs[first - 1] != "app":
            return True
    if name in ALLOW_FILE_NAMES:
        return False
    return any(fnmatch.fnmatch(name, g) for g in DENY_FILE_GLOBS)


def wants_lf(arcname: str) -> bool:
    name = arcname.rsplit("/", 1)[-1]
    return name in LF_NAMES or Path(name).suffix in LF_SUFFIXES


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


class DryRunArchive:
    """Stands in for ZipFile under --dry-run: records what would be written."""

    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.names: list[str] = []
        self.size = 0

    def __enter__(self) -> "DryRunArchive":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def writestr(self, arcname, data, **_kwargs) -> None:
        name = arcname.filename if isinstance(arcname, zipfile.ZipInfo) else arcname
        self.names.append(name)
        self.size += len(data)


def write_file(archive, path: Path, arcname: str) -> None:
    data = path.read_bytes()
    if b"\r\n" in data and (
        wants_lf(arcname)
        or (Path(arcname).suffix.lower() not in CRLF_SUFFIXES and looks_like_text(data))
    ):
        data = data.replace(b"\r\n", b"\n")
    info = zipfile.ZipInfo.from_file(path, arcname)
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def add_path(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: str,
    *,
    skip_prefixes: tuple[str, ...] = (),
    skip_script_helpers: bool = False,
) -> None:
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            rel_posix = path.relative_to(source).as_posix()
            full_arc = f"{arcname}/{rel_posix}" if arcname else rel_posix
            if is_denied(full_arc):
                continue
            if any(full_arc == p.rstrip("/") or full_arc.startswith(p) for p in skip_prefixes):
                continue
            if skip_script_helpers and should_skip_platform_path(full_arc):
                continue
            write_file(archive, path, full_arc)
    elif not is_denied(arcname):
        write_file(archive, source, arcname)


def _open_archive(dest: Path, dry_run: bool):
    if dry_run:
        return DryRunArchive(dest)
    return zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED)


def build_app_zip(app_id: str, out_dir: Path, *, dry_run: bool = False):
    app_dir = ROOT / "apps" / app_id
    if not app_dir.is_dir():
        raise SystemExit(f"Missing app directory {app_dir}")
    version = read_app_version(app_dir)
    dest = out_dir / f"stonepi-{app_id}-{version}.zip"
    with _open_archive(dest, dry_run) as archive:
        archive.writestr("STONEPI.txt", STONEPI_NOTICE)
        for name in APP_INCLUDE:
            source = app_dir / name
            if not source.exists():
                continue
            add_path(archive, source, name)
    return archive if dry_run else dest


def build_platform_zip(out_dir: Path, *, dry_run: bool = False):
    version = read_platform_version()
    dest = out_dir / f"stonepi-platform-{version}.zip"
    with _open_archive(dest, dry_run) as archive:
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
    return archive if dry_run else dest


# --- pre-flight -------------------------------------------------------------------------------

PREFLIGHT_DIRS = ("apps", "packages", "deploy")
INSTALL_SH = ROOT / "deploy" / "install.sh"
DEST_PATH_RE = re.compile(r"\$DEST/([A-Za-z0-9_./-]+)")


def _git(*args: str) -> str | None:
    """stdout of a read-only git command in ROOT, or None outside a checkout / without git."""
    if not (ROOT / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def install_sh_paths() -> list[str]:
    """Repo paths deploy/install.sh copies or runs from $DEST (/opt/stonepi).

    Skips what the installer creates itself (venvs, runtime data/ folders; bundled app/data/ is
    kept) and bare top-level prefixes such as ``$DEST/apps/``.
    """
    if not INSTALL_SH.exists():
        return []
    found = set()
    for raw in DEST_PATH_RE.findall(INSTALL_SH.read_text(encoding="utf-8", errors="replace")):
        rel = raw.rstrip("/.")
        if not rel or (raw.endswith("/") and "/" not in rel):
            continue
        parts = rel.split("/")
        if ".venv" in parts:
            continue
        if parts[-1] == "data" and not (len(parts) >= 2 and parts[-2] == "app"):
            continue
        found.add(rel)
    return sorted(found)


def untracked_files() -> list[str]:
    out = _git("status", "--porcelain", "--untracked-files=all", "--", *PREFLIGHT_DIRS)
    if out is None:
        return []
    return sorted(line[3:].strip().strip('"') for line in out.splitlines() if line.startswith("?? "))


def _absent(paths: list[str], listing: set[str]) -> list[str]:
    """Paths absent from a git file listing (a path may name a file or a directory)."""
    dirs = set()
    for name in listing:
        parts = name.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return [p for p in paths if p not in listing and p not in dirs]


def preflight(*, allow_dirty: bool) -> None:
    problems: list[str] = []
    in_checkout = _git("rev-parse", "--is-inside-work-tree") is not None
    if in_checkout:
        loose = untracked_files()
        if loose:
            problems.append(
                f"{len(loose)} untracked file(s) under {'/, '.join(PREFLIGHT_DIRS)}/ would ship in these zips "
                "but be missing from the git-archive tag tarball the web installer downloads "
                "(commit or gitignore them, or pass --allow-dirty):\n    "
                + "\n    ".join(loose)
            )
    else:
        print("pre-flight: not a git checkout (tag export?); skipping the untracked-file check")

    referenced = install_sh_paths()
    missing = [p for p in referenced if not (ROOT / p).exists()]
    if missing:
        problems.append("deploy/install.sh references paths missing from this tree:\n    " + "\n    ".join(missing))
    if in_checkout and referenced:
        tracked = set((_git("ls-files") or "").splitlines())
        not_tracked = [p for p in _absent(referenced, tracked) if p not in missing]
        if not_tracked:
            problems.append(
                "deploy/install.sh references paths not tracked by git (absent from a tag archive):\n    "
                + "\n    ".join(not_tracked)
            )
        tag = f"v{read_platform_version()}"
        if _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}") is not None:
            in_tag = set((_git("ls-tree", "-r", "--name-only", tag) or "").splitlines())
            absent = _absent(referenced, in_tag)
            if absent:
                problems.append(f"tag {tag} lacks paths deploy/install.sh references:\n    " + "\n    ".join(absent))
            else:
                print(f"pre-flight: tag {tag} contains all {len(referenced)} install.sh paths")
        else:
            print(f"pre-flight: tag {tag} does not exist yet; re-run after tagging to check the tag itself")
    if not problems:
        print(f"pre-flight: ok ({len(referenced)} install.sh paths present)")
        return
    for problem in problems:
        print(f"pre-flight: {'WARNING' if allow_dirty else 'ERROR'}: {problem}", file=sys.stderr)
    if not allow_dirty:
        raise SystemExit("pre-flight failed; fix the above or re-run with --allow-dirty")


def versions_table() -> str:
    """Markdown for the root CHANGELOG "App versions in this cut" section."""
    lines = [f"Platform: **{read_platform_version()}**", ""]
    for group, label in (("system", "System (ships with platform)"), ("user", "Apps")):
        items = [item for item in APP_CATALOG if (item.get("group") or "user") == group]
        if not items:
            continue
        lines += [f"| {label} | Version |", "|-----|---------|"]
        for item in items:
            version = read_app_version(ROOT / "apps" / item["id"])
            lines.append(f"| {item['id']} | {version} |")
        if group == "system":
            for app_id in _load_service_apps():
                lines.append(f"| {app_id} | {read_app_version(ROOT / 'apps' / app_id)} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "releases")
    parser.add_argument("--apps", default=",".join(APP_IDS), help="Comma-separated app ids")
    parser.add_argument("--platform", action="store_true", help="Also build stonepi-platform-*.zip")
    parser.add_argument("--all", action="store_true", help="Build every app zip and the platform pack")
    parser.add_argument("--versions-table", action="store_true", help="Print the CHANGELOG app versions table and exit")
    parser.add_argument("--checksums", action="store_true", help="Only rewrite SHA256SUMS for the assets already in --out")
    parser.add_argument("--dry-run", action="store_true", help="Run the pre-flight and list what each zip would hold; write nothing")
    parser.add_argument("--verbose", "-v", action="store_true", help="With --dry-run, list every file")
    parser.add_argument("--allow-dirty", action="store_true", help="Downgrade pre-flight failures to warnings")
    args = parser.parse_args()
    if args.versions_table:
        print(versions_table(), end="")
        return
    out_dir = args.out.resolve()
    if args.checksums:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_checksums(out_dir)
        return
    apps = [item.strip() for item in args.apps.split(",") if item.strip()]
    if args.all:
        apps = list(APP_IDS)
    for app_id in apps:
        if app_id not in APP_IDS:
            raise SystemExit(f"Unknown app id: {app_id}")
    preflight(allow_dirty=args.allow_dirty)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(lambda a=app_id: build_app_zip(a, out_dir, dry_run=args.dry_run)) for app_id in apps]
    if args.platform or args.all:
        jobs.append(lambda: build_platform_zip(out_dir, dry_run=args.dry_run))
    built = []
    for job in jobs:
        result = job()
        if args.dry_run:
            print(f"would write {result.dest.name}: {len(result.names)} files, {result.size:,} bytes uncompressed")
            if args.verbose:
                for name in result.names:
                    print(f"    {name}")
            built.append(result.dest)
            continue
        built.append(result)
        try:
            shown = result.relative_to(ROOT)
        except ValueError:
            shown = result
        print(f"wrote {shown}")
    if args.dry_run:
        print(f"dry run: {len(built)} package(s) not written")
        return
    print(f"{len(built)} package(s) in {out_dir}")
    write_checksums(out_dir)


def write_checksums(out_dir: Path) -> Path:
    """SHA256SUMS (sha256sum format) over every release asset in out_dir; the updater checks it."""
    rows = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name.startswith("stonepi-") and path.name.endswith((".zip", ".tar.gz")):
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    dest = out_dir / "SHA256SUMS"
    dest.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote SHA256SUMS ({len(rows)} files)")
    return dest


if __name__ == "__main__":
    main()
