import pytest

from app.services import settings


@pytest.fixture(autouse=True)
def _isolated_platform_state(tmp_path, monkeypatch):
    """Keep tests off machine-wide StonePi state.

    - Vault-backed settings (x3_sync_token, API keys) would otherwise be written
      to STONEPI_VAULT_DIR or /var/lib/stonepi/vault and leak between runs.
    - A leftover repo data/exposure file would otherwise override the
      STONEPI_EXPOSURE env that exposure tests set.
    """
    monkeypatch.setenv("STONEPI_EXPOSURE_FILE", str(tmp_path / "exposure"))
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
