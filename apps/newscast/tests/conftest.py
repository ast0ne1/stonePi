import atexit
import os
import shutil
import tempfile

# Tests get their own throwaway data folder and database instead of the app default
# (./data/newscast.db), which is the same file the Windows run-dev copy uses.
# Set before ``app`` is imported below.
_TEST_DATA = tempfile.mkdtemp(prefix="newscast-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ["STONEPI_DATA_DIR"] = _TEST_DATA
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DATA, "newscast.db").replace("\\", "/")
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)

import pytest  # noqa: E402

from app.services import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_platform_state(tmp_path, monkeypatch):
    """Keep tests off machine-wide StonePi state.

    - Vault-backed settings (x3_sync_token, API keys) would otherwise be written
      to STONEPI_VAULT_DIR or /var/lib/stonepi/vault and leak between runs.
    - A leftover repo data/exposure file would otherwise override the
      STONEPI_EXPOSURE env that exposure tests set.
    """
    monkeypatch.setenv("STONEPI_EXPOSURE_FILE", str(tmp_path / "exposure"))
    # Platform AI (stonepi_ai): its config, health and quota files in tmp, and no provider keys
    # unless a test sets some (see tests/ai_helpers.py).
    monkeypatch.setenv("STONEPI_AI_DIR", str(tmp_path / "ai"))
    try:
        from stonepi_ai import config as ai_config

        monkeypatch.setattr(ai_config, "secret_for", lambda _pid: "")
    except ImportError:
        pass
    from app.services import reader_push

    reader_push.reset_probe_state()  # remembered reader probes are module state
    monkeypatch.delenv("STONEPI_EXPOSURE", raising=False)
    try:
        import stonepi_vault.store as store
    except ImportError:
        yield
        return
    for env_name in settings.VAULT_SECRET_KEYS.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(store, "_vault", store.Vault(tmp_path / "vault"))
    yield
