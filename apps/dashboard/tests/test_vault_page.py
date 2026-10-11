"""Settings → Vault: one grouped list, each secret once, with counts and edit-in-place rows."""

from __future__ import annotations

from app import routes
from tests.test_recover_password_settings import FakeVault, _client


def _page(monkeypatch, tmp_path, data):
    monkeypatch.setattr(routes, "RECOVER_PASSWD_FILE", tmp_path / "recover.passwd")
    resp = _client(monkeypatch, FakeVault(data), []).get("/settings?tab=vault")
    assert resp.status_code == 200
    return resp.text


def test_each_secret_appears_once_with_counts(monkeypatch, tmp_path):
    html = _page(monkeypatch, tmp_path, {"OPENAI_API_KEY": "x", "GOOGLE_CLIENT_ID": "y"})
    total = sum(len(g["items"]) for g in routes.VAULT_KEY_CATALOG if not g.get("legacy"))
    assert f"2 of {total}" in html
    assert html.count('id="vault-OPENAI_API_KEY"') == 1
    assert 'data-vault-state="set"' in html and 'data-vault-state="missing"' in html
    # The old dropdown form is gone.
    assert "data-vault-key" not in html


def test_notify_owned_secrets_link_out_instead_of_editing(monkeypatch, tmp_path):
    html = _page(monkeypatch, tmp_path, {})
    assert "Set in Phone alerts" in html and "Set in Displays" in html
    assert 'id="vault-form-STONEPI_NTFY_TOKEN"' not in html
    assert 'id="vault-form-DISPLAY_WEBHOOK_URL"' not in html
    assert 'id="vault-form-ANTHROPIC_API_KEY"' in html


def test_custom_legacy_and_extra_display_keys_listed_under_other(monkeypatch, tmp_path):
    html = _page(
        monkeypatch,
        tmp_path,
        {"MY_KEY": "1", "NEWSCAST_NTFY_TOKEN": "2", "DISPLAY_WEBHOOK_URL_KITCHEN": "3"},
    )
    assert "Other stored secrets" in html
    assert 'id="vault-MY_KEY"' in html
    assert "NewsCast ntfy (legacy)" in html
    assert "TRMNL webhook — Kitchen" in html
    # Extra keys don't count towards the "x of y" total.
    assert "0 of " in html


def test_recover_password_shown_set_from_file(monkeypatch, tmp_path):
    (tmp_path / "recover.passwd").write_text("stonepi:x\n", encoding="utf-8")
    monkeypatch.setattr(routes, "RECOVER_PASSWD_FILE", tmp_path / "recover.passwd")
    resp = _client(monkeypatch, FakeVault(), []).get("/settings?tab=vault")
    assert "1 of " in resp.text
    assert 'minlength="12"' in resp.text
