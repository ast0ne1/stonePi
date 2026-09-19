#!/usr/bin/env python3
"""Serve the StonePi tree on the LAN and offer a transfer zip.

Usage (from repo root):
  python scripts/lan-serve.py
  python scripts/lan-serve.py --port 8765

Then open http://<this-pc-ip>:8765/deploy/walkthrough.html
"""

from __future__ import annotations

import argparse
import io
import socket
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "data",
    ".cursor",
    "agent-transcripts",
}
SKIP_FILE_SUFFIXES = (".pyc", ".pyo", ".zip")
SKIP_FILE_NAMES = {"stonePi-transfer.zip", ".DS_Store"}


def should_skip(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = set(rel.parts)
    if parts & SKIP_DIR_NAMES:
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    if path.suffix.lower() in SKIP_FILE_SUFFIXES:
        return True
    return False


def build_zip(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if should_skip(path, root):
                continue
            arc = Path("stonePi") / path.relative_to(root)
            zf.write(path, arc.as_posix())
    return buf.getvalue()


def lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        ips.append("127.0.0.1")
    return ips


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):  # noqa: N802
        if self.path.split("?", 1)[0] in {"/stonePi-transfer.zip", "/download.zip"}:
            try:
                payload = build_zip(ROOT)
            except Exception as exc:  # pragma: no cover
                body = f"Failed to build zip: {exc}\n".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="stonePi-transfer.zip"',
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        return super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN file server + StonePi transfer zip")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    handler = partial(Handler, directory=str(ROOT))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("Serving", ROOT)
    print("Walkthrough:")
    for ip in lan_ips():
        print(f"  http://{ip}:{args.port}/deploy/walkthrough.html")
    print("Zip download:")
    for ip in lan_ips():
        print(f"  http://{ip}:{args.port}/stonePi-transfer.zip")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
