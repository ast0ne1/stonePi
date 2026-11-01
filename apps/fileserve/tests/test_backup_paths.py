"""Backup restore must not write outside hosted/ or tls/ (zip slip)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services import backup


def test_safe_zip_target_rejects_dotdot(tmp_path: Path):
    dest = tmp_path / "hosted"
    dest.mkdir()
    with pytest.raises(ValueError, match="Unsafe path"):
        backup._safe_zip_target(dest, "../outside.txt")
    with pytest.raises(ValueError, match="Unsafe path"):
        backup._safe_zip_target(dest, "a/../../outside.txt")
    ok = backup._safe_zip_target(dest, "site/index.html")
    assert ok == (dest / "site" / "index.html").resolve()


def test_restore_rejects_hosted_zip_slip(tmp_path, monkeypatch):
    data = tmp_path / "data"
    hosted = data / "hosted"
    data.mkdir()
    hosted.mkdir()
    (data / "fileserve.db").write_bytes(b"")
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "HOSTED_DIR", hosted)
    monkeypatch.setattr(backup, "TLS_DIR", data / "tls")
    monkeypatch.setattr(backup, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(backup, "BACKUPS_DIR", data / "backups")
    monkeypatch.setattr(backup.database, "engine", None)

    evil = data / "backups" / "evil.zip"
    evil.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("manifest.json", '{"format":"fileserve-backup"}')
        archive.writestr("fileserve.db", b"")
        archive.writestr("hosted/../../pwned.txt", b"nope")
    with pytest.raises(ValueError, match="Unsafe path"):
        backup.restore_backup(evil)
    assert not (tmp_path / "pwned.txt").exists()
