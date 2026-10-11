from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

# Tests get their own throwaway data folder and database. Without this they used the
# app default (./data/eventtrakr.db), the same file the Windows run-dev copy uses, and
# left test accounts and events in it. Set before any test module imports ``app``.
_TEST_DATA = tempfile.mkdtemp(prefix="eventtrakr-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ["STONEPI_DATA_DIR"] = _TEST_DATA
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DATA, "eventtrakr.db").replace("\\", "/")
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_brightdata(tmp_path, monkeypatch):
    """Bright Data usage and cached results go to a temp folder, never the shared Vault."""
    import stonepi_vault.quota as quota_mod

    from app.services import brightdata

    real_get_quota = quota_mod.get_quota
    monkeypatch.setattr(quota_mod, "get_quota", lambda provider="brightdata", root=None: real_get_quota(provider, tmp_path / "vault"))
    monkeypatch.setattr(brightdata, "_cache_dir", lambda: tmp_path / "brightdata-cache")
