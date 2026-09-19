from __future__ import annotations

import os
from pathlib import Path

from stonepi_auth.http import exposure_mode, set_exposure_mode


def test_exposure_defaults_to_lan(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("STONEPI_EXPOSURE", raising=False)
    monkeypatch.delenv("STONEPI_EXPOSURE_FILE", raising=False)
    monkeypatch.setenv("STONEPI_EXPOSURE_FILE", str(tmp_path / "missing" / "exposure"))
    assert exposure_mode() == "lan"


def test_exposure_env_public(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STONEPI_EXPOSURE_FILE", str(tmp_path / "missing" / "exposure"))
    monkeypatch.setenv("STONEPI_EXPOSURE", "public")
    assert exposure_mode() == "public"


def test_set_exposure_mode_overrides_env(monkeypatch, tmp_path: Path):
    path = tmp_path / "exposure"
    monkeypatch.setenv("STONEPI_EXPOSURE_FILE", str(path))
    monkeypatch.setenv("STONEPI_EXPOSURE", "lan")
    set_exposure_mode("public")
    assert path.read_text(encoding="utf-8").strip() == "public"
    assert exposure_mode() == "public"
    set_exposure_mode("lan")
    assert exposure_mode() == "lan"
