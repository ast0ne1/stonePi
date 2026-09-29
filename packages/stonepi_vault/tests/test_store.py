from __future__ import annotations

from pathlib import Path

import stonepi_vault.store as store_mod
from stonepi_vault.store import Vault, configure, get_secret, get_vault


def test_vault_get_set_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STONEPI_VAULT_DIR", str(tmp_path))
    configure(tmp_path)
    vault = get_vault()
    vault.set("STONEPI_SESSION_SECRET", "s3cret")
    assert vault.get("STONEPI_SESSION_SECRET") == "s3cret"
    assert get_secret("STONEPI_SESSION_SECRET") == "s3cret"


def test_vault_load_uses_ttl_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STONEPI_VAULT_DIR", str(tmp_path))
    vault = Vault(tmp_path)
    vault.set("k", "v1")
    assert vault.get("k") == "v1"

    calls = {"n": 0}
    real_load_disk = vault._load_disk

    def counting_load_disk():
        calls["n"] += 1
        return real_load_disk()

    monkeypatch.setattr(vault, "_load_disk", counting_load_disk)
    vault._invalidate_cache()
    assert vault.get("k") == "v1"
    assert calls["n"] == 1
    assert vault.get("k") == "v1"
    assert calls["n"] == 1  # served from TTL cache


def test_vault_set_invalidates_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STONEPI_VAULT_DIR", str(tmp_path))
    vault = Vault(tmp_path)
    vault.set("k", "v1")
    assert vault.get("k") == "v1"
    vault.set("k", "v2")
    assert vault.get("k") == "v2"


def test_vault_cache_expires(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STONEPI_VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(store_mod, "_CACHE_TTL_SECONDS", 0.01)
    vault = Vault(tmp_path)
    vault.set("k", "v1")
    assert vault.get("k") == "v1"

    calls = {"n": 0}
    real_load_disk = vault._load_disk

    def counting_load_disk():
        calls["n"] += 1
        return real_load_disk()

    monkeypatch.setattr(vault, "_load_disk", counting_load_disk)
    vault._invalidate_cache()
    assert vault.get("k") == "v1"
    assert calls["n"] == 1

    # Force TTL expiry
    vault._cache_at = 0.0
    assert vault.get("k") == "v1"
    assert calls["n"] == 2
