from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_brightdata(tmp_path, monkeypatch):
    """Bright Data usage and cached results go to a temp folder, never the shared Vault."""
    import stonepi_vault.quota as quota_mod

    from app.services import brightdata

    real_get_quota = quota_mod.get_quota
    monkeypatch.setattr(quota_mod, "get_quota", lambda provider="brightdata", root=None: real_get_quota(provider, tmp_path / "vault"))
    monkeypatch.setattr(brightdata, "_cache_dir", lambda: tmp_path / "brightdata-cache")
