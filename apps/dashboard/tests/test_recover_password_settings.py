"""Settings → Vault → Recover password goes to recover.passwd via the helper, never the Vault."""

from __future__ import annotations

from fastapi.testclient import TestClient
from stonepi_auth.session import PlatformUser

from app import routes, services
from app.main import app

ADMIN = PlatformUser(user_id="1", username="admin", display_name="Admin", is_admin=True, apps=[])


class FakeVault:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def set(self, key, value):
        self.data[key] = value

    def get(self, key, default=""):
        return self.data.get(key, default)

    def delete(self, key):
        return self.data.pop(key, None) is not None

    def list_keys(self):
        return sorted(self.data)


def _client(monkeypatch, vault, calls, code=0):
    import stonepi_vault

    monkeypatch.setattr(routes, "_user_or_login", lambda request, **kw: (ADMIN, None))
    monkeypatch.setattr(routes, "_require_csrf", lambda request, form: True)
    monkeypatch.setattr(stonepi_vault, "get_vault", lambda: vault)

    def helper(args, timeout=120, stdin=None):
        calls.append(list(args) + ([stdin] if stdin is not None else []))
        return code, "" if code == 0 else "sudo: helper missing"

    monkeypatch.setattr(services, "_helper_run", helper)
    return TestClient(app)


def test_saving_recover_password_calls_helper_and_skips_vault(monkeypatch):
    vault = FakeVault({"STONEPI_RECOVER_PASSWORD": "old-copy"})
    calls: list = []
    resp = _client(monkeypatch, vault, calls).post(
        "/settings/vault",
        data={"key": "STONEPI_RECOVER_PASSWORD", "value": "hunter22-horse", "action": "set"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    # The password goes on stdin, never argv (visible in ps).
    assert calls == [["recover-passwd-set", "hunter22-horse\n"]]
    # Never stored, and any legacy copy is removed.
    assert "STONEPI_RECOVER_PASSWORD" not in vault.data


def test_recover_password_shorter_than_12_is_refused(monkeypatch):
    vault = FakeVault()
    calls: list = []
    client = _client(monkeypatch, vault, calls)
    resp = client.post(
        "/settings/vault",
        data={"key": "STONEPI_RECOVER_PASSWORD", "value": "  elevenchars  ", "action": "set"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    assert "at%20least%2012%20characters" in resp.headers["location"]
    assert calls == [] and vault.data == {}
    ok = client.post(
        "/settings/vault",
        data={"key": "STONEPI_RECOVER_PASSWORD", "value": "twelve-chars", "action": "set"},
        follow_redirects=False,
    )
    assert "msg=" in ok.headers["location"]
    assert calls == [["recover-passwd-set", "twelve-chars\n"]]


def test_recover_password_helper_failure_is_reported(monkeypatch):
    vault = FakeVault()
    calls: list = []
    resp = _client(monkeypatch, vault, calls, code=1).post(
        "/settings/vault",
        data={"key": "STONEPI_RECOVER_PASSWORD", "value": "hunter22-horse", "action": "set"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and "err=" in resp.headers["location"]
    assert vault.data == {}


def test_clearing_recover_password_uses_helper(monkeypatch):
    vault = FakeVault()
    calls: list = []
    resp = _client(monkeypatch, vault, calls).post(
        "/settings/vault",
        data={"key": "STONEPI_RECOVER_PASSWORD", "action": "delete"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    assert calls == [["recover-passwd-clear"]]


def test_other_vault_keys_still_saved(monkeypatch):
    vault = FakeVault()
    calls: list = []
    resp = _client(monkeypatch, vault, calls).post(
        "/settings/vault",
        data={"key": "OPENAI_API_KEY", "value": "sk-test", "action": "set"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert vault.data == {"OPENAI_API_KEY": "sk-test"}
    assert calls == []


def test_recover_password_status_comes_from_file(monkeypatch, tmp_path):
    passwd = tmp_path / "recover.passwd"
    monkeypatch.setattr(routes, "RECOVER_PASSWD_FILE", passwd)
    assert routes._recover_password_set() is False
    passwd.write_text("stonepi:x\n", encoding="utf-8")
    assert routes._recover_password_set() is True
