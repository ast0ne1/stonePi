"""Recover: CSRF, login rate limits, restore path checks and passwd-file-only credentials."""

from __future__ import annotations

import os
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from app import main as recover

PASSWORD = "correct-horse"
PROXY_TOKEN = "a" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    passwd = tmp_path / "recover.passwd"
    passwd.write_text(f"stonepi:{PASSWORD}\n", encoding="utf-8")
    monkeypatch.setattr(recover, "PASSWD_FILE", passwd)
    monkeypatch.setattr(recover, "LEGACY_PASSWD_FILE", tmp_path / "recovery.passwd")
    monkeypatch.setattr(recover, "_dev_mode", lambda: False)
    monkeypatch.setattr(recover, "_session_admin", lambda request: False)
    monkeypatch.setattr(recover, "_unit_rows", lambda: [])
    monkeypatch.setattr(recover, "_list_snapshots", lambda: [])
    monkeypatch.setattr(recover, "_disk_pct", lambda: "n/a")
    monkeypatch.setattr(recover, "migrate_legacy_password", lambda: None)
    token_file = tmp_path / "recover-proxy.token"
    token_file.write_text(PROXY_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setattr(recover, "PROXY_TOKEN_FILE", token_file)
    monkeypatch.setattr(recover, "_proxy_token_cache", None)
    recover._login_failures.clear()
    ran: list = []

    def fake_run(cmd, timeout=30):
        ran.append(cmd)
        return 0, ""

    monkeypatch.setattr(recover, "_run", fake_run)
    c = TestClient(recover.app)
    c.ran = ran
    return c


def _csrf(client) -> str:
    client.get("/login")
    return client.cookies.get(recover.CSRF_COOKIE)


def _login(client, password=PASSWORD):
    token = _csrf(client)
    return client.post(
        "/login",
        data={"username": "stonepi", "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def test_login_page_includes_csrf_field(client):
    page = client.get("/login")
    token = client.cookies.get(recover.CSRF_COOKIE)
    assert token and f'name="csrf_token" value="{token}"' in page.text


def test_login_without_csrf_is_rejected(client):
    resp = client.post("/login", data={"username": "stonepi", "password": PASSWORD}, follow_redirects=False)
    assert resp.status_code == 400
    assert recover.SESSION_COOKIE not in resp.cookies


def test_login_and_action_require_csrf(client):
    assert _login(client).status_code == 303
    home = client.get("/")
    assert home.status_code == 200
    assert 'name="csrf_token"' in home.text

    forged = client.post("/action", data={"op": "reboot"}, follow_redirects=False)
    assert forged.status_code == 303 and "err=" in forged.headers["location"]
    assert client.ran == []

    token = client.cookies.get(recover.CSRF_COOKIE)
    ok = client.post("/action", data={"op": "reboot", "csrf_token": token}, follow_redirects=False)
    assert ok.status_code == 303 and "Reboot" in ok.headers["location"]
    assert client.ran == [["systemctl", "reboot"]]


def test_logout_and_restore_require_csrf(client, tmp_path, monkeypatch):
    _login(client)
    assert "err=" in client.post("/logout", follow_redirects=False).headers["location"]
    local = tmp_path / "current"
    local.mkdir()
    monkeypatch.setattr(recover, "LOCAL_BACKUP_DIR", local)
    forged = client.post("/restore", data={"path": str(local)}, follow_redirects=False)
    assert "err=" in forged.headers["location"]
    assert client.ran == []
    token = client.cookies.get(recover.CSRF_COOKIE)
    ok = client.post("/restore", data={"path": str(local), "csrf_token": token}, follow_redirects=False)
    assert "msg=" in ok.headers["location"]
    assert client.ran == [[recover.HELPER, "restore", str(local.resolve())]]


def test_login_rate_limited_per_ip(client):
    for _ in range(recover.LOGIN_MAX_FAILURES_PER_IP):
        assert _login(client, "wrong").status_code == 401
    blocked = _login(client)  # even the right password is refused while locked out
    assert blocked.status_code == 429


def test_login_rate_limited_globally(client, monkeypatch):
    monkeypatch.setattr(recover, "LOGIN_MAX_FAILURES_PER_IP", 10_000)
    now = time.time()
    recover._login_failures["global"].extend([now] * recover.LOGIN_MAX_FAILURES_GLOBAL)
    assert _login(client).status_code == 429


class _Req:
    def __init__(self, peer, real, proxy_token=None):
        self.client = types.SimpleNamespace(host=peer)
        self.headers = {"x-real-ip": real}
        if proxy_token is not None:
            self.headers["x-stonepi-proxy"] = proxy_token


def test_x_real_ip_only_trusted_from_loopback_with_proxy_token(client):
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9", PROXY_TOKEN)) == "192.168.1.9"
    assert recover._client_ip(_Req("::1", "192.168.1.9", PROXY_TOKEN)) == "192.168.1.9"
    # A LAN client can't get its X-Real-IP trusted, token or not.
    assert recover._client_ip(_Req("192.168.1.50", "10.0.0.1")) == "192.168.1.50"
    assert recover._client_ip(_Req("192.168.1.50", "10.0.0.1", PROXY_TOKEN)) == "192.168.1.50"


def test_forged_x_real_ip_from_loopback_is_keyed_on_peer(client):
    """Any local process without nginx's token shares the one loopback bucket."""
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9")) == "127.0.0.1"
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9", "")) == "127.0.0.1"
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9", "b" * 64)) == "127.0.0.1"
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9", PROXY_TOKEN[:-1])) == "127.0.0.1"


def test_rotating_forged_x_real_ip_hits_per_ip_lockout(client):
    for n in range(recover.LOGIN_MAX_FAILURES_PER_IP):
        forged = TestClient(recover.app, client=("127.0.0.1", 50000), headers={"x-real-ip": f"10.9.0.{n}"})
        assert _login(forged, "wrong").status_code == 401
    rotated = TestClient(recover.app, client=("127.0.0.1", 50000), headers={"x-real-ip": "10.9.0.200"})
    assert _login(rotated).status_code == 429
    assert len(recover._login_failures["ip:127.0.0.1"]) == recover.LOGIN_MAX_FAILURES_PER_IP


def test_proxied_logins_with_token_are_keyed_per_client(client):
    for n in range(recover.LOGIN_MAX_FAILURES_PER_IP):
        via_nginx = TestClient(
            recover.app,
            client=("127.0.0.1", 50000),
            headers={"x-real-ip": "192.168.1.20", "x-stonepi-proxy": PROXY_TOKEN},
        )
        assert _login(via_nginx, "wrong").status_code == 401
    assert "ip:127.0.0.1" not in recover._login_failures
    other = TestClient(
        recover.app,
        client=("127.0.0.1", 50000),
        headers={"x-real-ip": "192.168.1.21", "x-stonepi-proxy": PROXY_TOKEN},
    )
    assert _login(other).status_code == 303


def test_missing_proxy_token_file_trusts_no_proxy_headers(client, tmp_path, monkeypatch):
    monkeypatch.setattr(recover, "PROXY_TOKEN_FILE", tmp_path / "absent.token")
    monkeypatch.setattr(recover, "_proxy_token_cache", None)
    req = _Req("127.0.0.1", "192.168.1.9", PROXY_TOKEN)
    assert recover._client_ip(req) == "127.0.0.1"
    assert recover._behind_proxy(req) is False
    empty = tmp_path / "empty.token"
    empty.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(recover, "PROXY_TOKEN_FILE", empty)
    assert recover._client_ip(_Req("127.0.0.1", "192.168.1.9", "")) == "127.0.0.1"


def test_proxy_token_reread_when_file_changes(client):
    token_file = recover.PROXY_TOKEN_FILE
    assert recover._behind_proxy(_Req("127.0.0.1", "x", PROXY_TOKEN))
    token_file.write_text("c" * 64 + "\n", encoding="utf-8")
    stat = token_file.stat()
    os.utime(token_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert not recover._behind_proxy(_Req("127.0.0.1", "x", PROXY_TOKEN))
    assert recover._behind_proxy(_Req("127.0.0.1", "x", "c" * 64))


def test_restore_path_resolution(tmp_path, monkeypatch):
    local = tmp_path / "current"
    local.mkdir()
    usb = tmp_path / "usb" / "RaspberryPi-Backup"
    snap = usb / "2026-10-01_0200"
    snap.mkdir(parents=True)
    outside = tmp_path / "etc"
    outside.mkdir()
    monkeypatch.setattr(recover, "LOCAL_BACKUP_DIR", local)
    monkeypatch.setattr(recover, "USB_BACKUP_ROOT", usb)

    assert recover._resolve_backup_path(str(local)) == local.resolve()
    assert recover._resolve_backup_path(str(snap)) == snap.resolve()
    assert recover._resolve_backup_path(str(usb)) is None
    assert recover._resolve_backup_path(str(usb / ".." / ".." / "etc")) is None
    assert recover._resolve_backup_path(str(outside)) is None
    assert recover._resolve_backup_path(str(usb / "missing")) is None
    assert recover._resolve_backup_path("") is None
    try:
        (usb / "escape").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # Windows without symlink privilege
    assert recover._resolve_backup_path(str(usb / "escape")) is None


def test_password_only_from_file_outside_dev(tmp_path, monkeypatch):
    passwd = tmp_path / "recover.passwd"
    monkeypatch.setattr(recover, "PASSWD_FILE", passwd)
    monkeypatch.setattr(recover, "LEGACY_PASSWD_FILE", tmp_path / "recovery.passwd")
    monkeypatch.setenv("STONEPI_RECOVER_PASSWORD", "from-env")
    monkeypatch.setattr(recover, "_dev_mode", lambda: False)
    assert recover._load_passwd() is None
    passwd.write_text("stonepi:from-file\n", encoding="utf-8")
    assert recover._load_passwd() == ("stonepi", "from-file")
    monkeypatch.setattr(recover, "_dev_mode", lambda: True)
    assert recover._load_passwd() == ("stonepi", "from-env")


class FakeVault:
    def __init__(self, data):
        self.data = dict(data)
        self.writes: list = []

    def get(self, key, default=""):
        return self.data.get(key, default)

    def set(self, key, value):
        self.writes.append(key)
        self.data[key] = value

    def delete(self, key):
        return self.data.pop(key, None) is not None


def _patch_vault(monkeypatch, vault):
    module = types.ModuleType("stonepi_vault")
    module.get_vault = lambda: vault
    module.set_secret = lambda key, value: vault.set(key, value)
    module.get_secret = lambda key, env_name=None, default="": vault.get(key, default)
    monkeypatch.setitem(sys.modules, "stonepi_vault", module)


def _passwd_paths(tmp_path, monkeypatch):
    passwd = tmp_path / "recover.passwd"
    monkeypatch.setattr(recover, "PASSWD_FILE", passwd)
    monkeypatch.setattr(recover, "LEGACY_PASSWD_FILE", tmp_path / "recovery.passwd")
    return passwd


def test_vault_password_moves_to_file_and_is_deleted(tmp_path, monkeypatch):
    passwd = _passwd_paths(tmp_path, monkeypatch)
    vault = FakeVault({"STONEPI_RECOVER_PASSWORD": "vault-pw", "STONEPI_RECOVERY_PASSWORD": "older"})
    _patch_vault(monkeypatch, vault)
    recover.migrate_legacy_password()
    assert passwd.read_text(encoding="utf-8").strip() == "stonepi:vault-pw"
    assert vault.data == {}
    if os.name != "nt":
        assert (passwd.stat().st_mode & 0o777) == 0o600


def test_legacy_vault_key_alone_is_migrated(tmp_path, monkeypatch):
    passwd = _passwd_paths(tmp_path, monkeypatch)
    vault = FakeVault({"STONEPI_RECOVERY_PASSWORD": "older"})
    _patch_vault(monkeypatch, vault)
    recover.migrate_legacy_password()
    assert passwd.read_text(encoding="utf-8").strip() == "stonepi:older"
    assert vault.data == {}


def test_migration_without_vault_copy_leaves_file(tmp_path, monkeypatch):
    passwd = _passwd_paths(tmp_path, monkeypatch)
    passwd.write_text("stonepi:keep\n", encoding="utf-8")
    _patch_vault(monkeypatch, FakeVault({}))
    recover.migrate_legacy_password()
    assert passwd.read_text(encoding="utf-8").strip() == "stonepi:keep"


def test_first_run_password_never_written_to_vault(tmp_path, monkeypatch):
    passwd = _passwd_paths(tmp_path, monkeypatch)
    vault = FakeVault({})
    _patch_vault(monkeypatch, vault)
    created = recover.ensure_recover_password()
    assert created and passwd.read_text(encoding="utf-8").strip() == f"stonepi:{created}"
    assert vault.writes == []
    assert recover.ensure_recover_password() is None


def _sso_admin(request):
    """Stand-in for a valid platform admin cookie: present means signed in."""
    return bool(request.cookies.get("stonepi"))


def test_logout_form_posts_relative_with_csrf(client):
    _login(client)
    home = client.get("/")
    token = client.cookies.get(recover.CSRF_COOKIE)
    assert '<form method="post" action="logout"' in home.text
    assert f'name="csrf_token" value="{token}"' in home.text


def test_logout_ends_recover_session(client):
    _login(client)
    token = client.cookies.get(recover.CSRF_COOKIE)
    resp = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "login?msg=signed-out"
    assert recover.SESSION_COOKIE not in client.cookies
    assert client.get("/", follow_redirects=False).headers["location"] == "login"
    assert "Signed out." in client.get("/login?msg=signed-out").text


def test_logout_direct_signs_out_platform_sso(client, monkeypatch):
    """On :8099 an SSO admin would bounce straight back in; the platform cookies are cleared too."""
    monkeypatch.setattr(recover, "_session_admin", _sso_admin)
    client.cookies.set("stonepi", "platform-session", domain="testserver.local")
    client.cookies.set("stonepi_csrf", "platform-csrf", domain="testserver.local")
    token = _csrf(client)
    assert client.get("/", follow_redirects=False).status_code == 200
    resp = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.headers["location"] == "login?msg=signed-out"
    assert "stonepi" not in client.cookies and "stonepi_csrf" not in client.cookies
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 200 and "Sign in" in login.text


def test_logout_via_nginx_signs_out_through_auth(client, monkeypatch):
    monkeypatch.setattr(recover, "_session_admin", _sso_admin)
    monkeypatch.setattr(recover, "_auth_up", lambda: True)
    proxied = TestClient(
        recover.app,
        client=("127.0.0.1", 50000),
        headers={"x-real-ip": "192.168.1.20", "x-stonepi-proxy": PROXY_TOKEN},
    )
    proxied.cookies.set("stonepi", "platform-session")
    proxied.get("/login")
    token = proxied.cookies.get(recover.CSRF_COOKIE)
    resp = proxied.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.headers["location"] == "/auth/logout?next=%2Frecover%2Flogin%3Fmsg%3Dsigned-out"
    # Auth must still receive the platform cookie so it can revoke the session.
    assert proxied.cookies.get("stonepi") == "platform-session"


def test_dashboard_link_follows_how_recover_was_reached(client):
    _login(client)
    assert 'href="http://testserver/" title="Dashboard"' in client.get("/").text
    proxied = TestClient(
        recover.app,
        client=("127.0.0.1", 50000),
        headers={"x-real-ip": "192.168.1.20", "x-stonepi-proxy": PROXY_TOKEN},
    )
    for name, value in client.cookies.items():
        proxied.cookies.set(name, value)
    assert 'href="/" title="Dashboard"' in proxied.get("/").text


def test_forged_proxy_headers_without_token_get_direct_links_and_logout(client, monkeypatch):
    """A local caller faking nginx's headers is treated as a direct :8099 client."""
    monkeypatch.setattr(recover, "_session_admin", _sso_admin)
    monkeypatch.setattr(recover, "_auth_up", lambda: True)
    forged = TestClient(
        recover.app,
        client=("127.0.0.1", 50000),
        headers={"x-real-ip": "192.168.1.20", "x-forwarded-host": "stonepi.local"},
    )
    forged.cookies.set("stonepi", "platform-session")
    assert 'href="http://testserver/" title="Dashboard"' in forged.get("/").text
    token = forged.cookies.get(recover.CSRF_COOKIE)
    resp = forged.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.headers["location"] == "login?msg=signed-out"
