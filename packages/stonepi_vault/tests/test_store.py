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


# -- several apps (service users) saving to one vault --------------------------------


def test_two_writers_keep_each_others_secrets(tmp_path: Path):
    """App A's read cache predates app B's save; A's save must not drop B's secret."""
    dashboard = Vault(tmp_path)
    notify = Vault(tmp_path)
    dashboard.set("STONEPI_SESSION_SECRET", "s")
    assert notify.get("STONEPI_SESSION_SECRET") == "s"  # notify now has a warm cache
    dashboard.set("BRIGHTDATA_API_KEY", "b")
    notify.set("STONEPI_NTFY_TOKEN", "t")  # stale cache: must re-read disk under the lock
    fresh = Vault(tmp_path)
    assert fresh.get("STONEPI_SESSION_SECRET") == "s"
    assert fresh.get("BRIGHTDATA_API_KEY") == "b"
    assert fresh.get("STONEPI_NTFY_TOKEN") == "t"


def test_delete_also_reads_fresh(tmp_path: Path):
    a, b = Vault(tmp_path), Vault(tmp_path)
    a.set("one", "1")
    assert b.get("one") == "1"
    a.set("two", "2")
    assert b.delete("one") is True
    assert Vault(tmp_path).list_keys() == ["two"]


def test_unreadable_store_is_never_overwritten(tmp_path: Path):
    import pytest

    vault = Vault(tmp_path)
    vault.set("keep", "me")
    (tmp_path / "vault.key").write_bytes(b"x" * 44)  # wrong key: the store no longer decrypts
    before = (tmp_path / "secrets.enc").read_bytes()
    with pytest.raises(Exception):
        Vault(tmp_path).set("new", "value")
    assert (tmp_path / "secrets.enc").read_bytes() == before


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path):
    vault = Vault(tmp_path)
    for i in range(3):
        vault.set(f"k{i}", str(i))
    assert not list(tmp_path.glob("*.tmp"))
    assert Vault(tmp_path).get("k2") == "2"


def test_files_are_group_writable(tmp_path: Path):
    import os
    import stat

    import pytest

    if os.name == "nt":
        pytest.skip("POSIX modes only")
    Vault(tmp_path).set("k", "v")
    for name in ("secrets.enc", "vault.key"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o660
