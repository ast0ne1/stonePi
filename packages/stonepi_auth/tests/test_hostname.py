from __future__ import annotations

from pathlib import Path

import pytest

import stonepi_auth.hostname as hostname_mod
from stonepi_auth.hostname import (
    normalize_hostname,
    platform_hostname,
    set_platform_hostname,
    valid_hostname,
)


@pytest.fixture(autouse=True)
def _clear_hostname_cache():
    hostname_mod._invalidate_hostname_cache()
    yield
    hostname_mod._invalidate_hostname_cache()


def test_normalize_hostname():
    assert normalize_hostname("NewsCast.local") == "newscast"
    assert normalize_hostname(" living-room ") == "living-room"
    assert normalize_hostname("STONEPI.") == "stonepi"


def test_valid_hostname():
    assert valid_hostname("newscast")
    assert valid_hostname("pi")
    assert valid_hostname("living-room")
    assert not valid_hostname("")
    assert not valid_hostname("-bad")
    assert not valid_hostname("has space")
    assert not valid_hostname("Bad_Name")


def test_platform_hostname_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("STONEPI_HOSTNAME", raising=False)
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(tmp_path / "missing" / "hostname"))
    assert platform_hostname() == "stonepi"


def test_platform_hostname_from_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(tmp_path / "missing" / "hostname"))
    monkeypatch.setenv("STONEPI_HOSTNAME", "kitchen-pi")
    assert platform_hostname() == "kitchen-pi"


def test_set_platform_hostname_overrides_env(monkeypatch, tmp_path: Path):
    path = tmp_path / "hostname"
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(path))
    monkeypatch.setenv("STONEPI_HOSTNAME", "stonepi")
    set_platform_hostname("living-room")
    assert path.read_text(encoding="utf-8").strip() == "living-room"
    assert platform_hostname() == "living-room"


def test_set_platform_hostname_rejects_invalid(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(tmp_path / "hostname"))
    with pytest.raises(ValueError):
        set_platform_hostname("has space")


def test_platform_hostname_ttl_cache(monkeypatch, tmp_path: Path):
    path = tmp_path / "hostname"
    monkeypatch.setenv("STONEPI_HOSTNAME_FILE", str(path))
    path.write_text("alpha\n", encoding="utf-8")
    assert platform_hostname() == "alpha"
    path.write_text("beta\n", encoding="utf-8")
    # Within TTL, still serve cached value
    assert platform_hostname() == "alpha"
    hostname_mod._hostname_cache_at = 0.0
    assert platform_hostname() == "beta"
