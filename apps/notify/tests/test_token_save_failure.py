"""A Vault write failure for the ntfy token must be reported, not silently dropped."""

from __future__ import annotations

import stonepi_vault

from app import phone_alerts


def test_save_token_reports_failure(monkeypatch):
    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied", "/var/lib/stonepi/vault/secrets.enc")

    monkeypatch.setattr(stonepi_vault, "set_secret", denied)
    assert phone_alerts._save_token("tk_secret") is False


def test_save_token_success(monkeypatch):
    saved = {}
    monkeypatch.setattr(stonepi_vault, "set_secret", lambda k, v: saved.__setitem__(k, v))
    assert phone_alerts._save_token("tk_secret") is True
    assert saved == {"STONEPI_NTFY_TOKEN": "tk_secret"}
