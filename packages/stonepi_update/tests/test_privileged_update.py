"""SHA256SUMS checks on download, and handing installs to the root helper on the Pi."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest

from stonepi_update import core
from stonepi_update.core import Updater, parse_checksums


def _zip_bytes(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("app/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr("app/main.py", "")
        archive.writestr("requirements.txt", "")
    return buf.getvalue()


def _github(monkeypatch, *, zip_body: bytes, sums: str | None):
    """Stand-in GitHub: releases/latest, the zip, and (optionally) SHA256SUMS."""
    assets = [{"name": "stonepi-pinboard-0.0.7.zip", "browser_download_url": "https://dl/zip"}]
    if sums is not None:
        assets.append({"name": "SHA256SUMS", "browser_download_url": "https://dl/sums"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": "v0.1.9", "assets": assets})
        if str(request.url) == "https://dl/zip":
            return httpx.Response(200, content=zip_body)
        if str(request.url) == "https://dl/sums":
            return httpx.Response(200, text=sums or "")
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(core.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _updater(tmp_path: Path, **kw) -> Updater:
    root = tmp_path / "app"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text('__version__ = "0.0.6"\n', encoding="utf-8")
    store: dict = {}
    return Updater(
        app_id="pinboard",
        root_dir=root,
        updates_dir=tmp_path / "updates",
        current_version="0.0.6",
        get_repo=lambda: "owner/stonePi",
        get_last_check=lambda: store.get("last", {}),
        save_last_check=lambda payload: store.__setitem__("last", payload) or payload,
        **kw,
    )


def test_parse_checksums_formats():
    text = "a" * 64 + "  stonepi-pinboard-0.0.7.zip\n" + "B" * 64 + " *dist/stonepi-platform-0.1.9.zip\nnoise\n"
    assert parse_checksums(text) == {
        "stonepi-pinboard-0.0.7.zip": "a" * 64,
        "stonepi-platform-0.1.9.zip": "b" * 64,
    }


def test_download_is_checked_against_sha256sums(tmp_path, monkeypatch):
    body = _zip_bytes("0.0.7")
    sha = hashlib.sha256(body).hexdigest()
    _github(monkeypatch, zip_body=body, sums=f"{sha}  stonepi-pinboard-0.0.7.zip\n")
    result = _updater(tmp_path).check_latest()
    assert result["valid"] is True and result["sha256"] == sha


def test_checksum_mismatch_discards_download(tmp_path, monkeypatch):
    _github(monkeypatch, zip_body=_zip_bytes("0.0.7"), sums="0" * 64 + "  stonepi-pinboard-0.0.7.zip\n")
    result = _updater(tmp_path).check_latest()
    assert result["ok"] is False and "does not match" in result["message"]
    assert not list((tmp_path / "updates" / "staging").glob("*.zip"))


def test_missing_sha256sums_blocks_pi_installs_only(tmp_path, monkeypatch):
    _github(monkeypatch, zip_body=_zip_bytes("0.0.7"), sums=None)
    pi = _updater(tmp_path / "pi", privileged_helper=Path("/usr/local/sbin/stonepi-update-helper"))
    assert "no SHA256SUMS" in pi.check_latest()["message"]
    dev = _updater(tmp_path / "dev")
    assert dev.check_latest()["valid"] is True  # dev / Windows keeps working without it


def test_install_goes_through_the_helper(tmp_path, monkeypatch):
    body = _zip_bytes("0.0.7")
    sha = hashlib.sha256(body).hexdigest()
    _github(monkeypatch, zip_body=body, sums=f"{sha}  stonepi-pinboard-0.0.7.zip\n")
    helper = Path("/usr/local/sbin/stonepi-update-helper")
    updater = _updater(tmp_path, privileged_helper=helper)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True, "version": "0.0.7", "message": "Updated to 0.0.7."}) + "\n", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = updater.install_latest()
    assert seen["cmd"][:4] == ["sudo", "-n", str(helper), "apply"]
    assert seen["cmd"][4] == "pinboard" and seen["cmd"][6] == sha
    assert result == {"ok": True, "version": "0.0.7", "message": "Updated to 0.0.7.", "restart": False}
    # Nothing was written by the unprivileged process itself.
    assert (tmp_path / "app" / "app" / "__init__.py").read_text() == '__version__ = "0.0.6"\n'


def test_helper_failure_is_reported(tmp_path, monkeypatch):
    body = _zip_bytes("0.0.7")
    sha = hashlib.sha256(body).hexdigest()
    _github(monkeypatch, zip_body=body, sums=f"{sha}  stonepi-pinboard-0.0.7.zip\n")
    updater = _updater(tmp_path, privileged_helper=Path("/usr/local/sbin/stonepi-update-helper"))
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="sudo: a password is required\n"),
    )
    result = updater.install_latest()
    assert result["ok"] is False and "sudo: a password is required" in result["message"]


def test_dev_install_uses_the_apps_own_venv(tmp_path, monkeypatch):
    updater = _updater(tmp_path)
    venv_python = tmp_path / "app" / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    (tmp_path / "app" / "requirements.txt").write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    updater._install_requirements()
    assert calls and calls[0][0] == str(venv_python)
