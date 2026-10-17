"""Recover home page (U11): status summary, problems first, grouped services, header (U17)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as recover

PASSWORD = "correct-horse"


def _show_output(states: dict[str, tuple[str, str]]) -> str:
    """Fake ``systemctl show`` output: one block per unit, in UNITS order."""
    blocks = []
    for unit in recover.UNITS:
        active, enabled = states.get(unit, ("active", "enabled"))
        load = "not-found" if active == "missing" else "loaded"
        blocks.append(
            f"Id={unit}.service\nActiveState={'inactive' if active == 'missing' else active}\n"
            f"UnitFileState={enabled}\nLoadState={load}"
        )
    return "\n\n".join(blocks) + "\n"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    passwd = tmp_path / "recover.passwd"
    passwd.write_text(f"stonepi:{PASSWORD}\n", encoding="utf-8")
    monkeypatch.setattr(recover, "PASSWD_FILE", passwd)
    monkeypatch.setattr(recover, "LEGACY_PASSWD_FILE", tmp_path / "recovery.passwd")
    monkeypatch.setattr(recover, "PROXY_TOKEN_FILE", tmp_path / "missing.token")
    monkeypatch.setattr(recover, "_proxy_token_cache", None)
    monkeypatch.setattr(recover, "_dev_mode", lambda: False)
    monkeypatch.setattr(recover, "migrate_legacy_password", lambda: None)
    monkeypatch.setattr(recover, "_disk_pct", lambda: "42%")
    recover._login_failures.clear()

    def build(states=None, *, failover=False, snapshots=None):
        show = _show_output(states or {})

        def fake_run(cmd, timeout=30):
            if cmd[:2] == ["systemctl", "show"]:
                return 0, show
            if cmd[:2] == ["ip", "-o"]:
                return 0, "default via 192.168.0.1 dev wlan0 proto dhcp metric 600"
            return 0, ""

        monkeypatch.setattr(recover, "_run", fake_run)
        monkeypatch.setattr(recover, "_failover_on", lambda: failover)
        monkeypatch.setattr(recover, "_list_snapshots", lambda: snapshots or [])
        client = TestClient(recover.app)
        client.get("/login")
        token = client.cookies.get(recover.CSRF_COOKIE)
        client.post("/login", data={"username": "stonepi", "password": PASSWORD, "csrf_token": token})
        return client

    return build


def test_unit_status_kinds():
    assert recover._unit_status({"ActiveState": "active"}) == ("Running", "ok")
    assert recover._unit_status({"ActiveState": "failed"}) == ("Failed", "bad")
    assert recover._unit_status({"ActiveState": "inactive", "UnitFileState": "enabled"}) == ("Stopped", "warn")
    # Car Thing / Kiwix are disabled while switched off: that's not a problem.
    assert recover._unit_status({"ActiveState": "inactive", "UnitFileState": "disabled"}) == ("Off", "off")
    assert recover._unit_status({"LoadState": "not-found", "ActiveState": "inactive"})[0] == "Not installed"
    assert recover._unit_status({}) == ("Unknown", "muted")


def test_all_running_is_one_line(make_client):
    states = {"stonepi-carthing": ("inactive", "disabled")}
    html = make_client(states).get("/").text
    running = len(recover.UNITS) - 1
    assert f"All {running} services running" in html
    assert "Nothing needs attention." in html
    assert f"Show all services ({running} running, 1 off)" in html
    assert "Disk</dt><dd>42% used" in html
    assert "Network</dt><dd>Wi-Fi" in html
    # Friendly names, grouped, with the unit id underneath.
    assert ">Car Thing panel<" in html and "stonepi-carthing" in html
    assert html.index(">Core<") < html.index(">Apps<") < html.index(">Background<")


def test_problems_listed_first(make_client):
    states = {"stonepi-newscast": ("failed", "enabled"), "stonepi-studio": ("inactive", "enabled")}
    html = make_client(states).get("/").text
    assert "2 services need attention" in html
    problems = html[: html.index("Show all services")]
    assert ">NewsCast<" in problems and ">Studio<" in problems and ">Dashboard<" not in problems


def test_stop_is_in_the_menu_and_never_offered_for_auth_or_nginx(make_client):
    html = make_client().get("/").text
    assert 'aria-label="Restart Dashboard"' in html
    auth_row = html[html.index(">Auth<") : html.index(">Dashboard<")]
    assert 'value="stop"' not in auth_row
    dash_row = html[html.index(">Dashboard<") : html.index(">Notify<")]
    assert 'class="row-menu"' in dash_row and 'value="stop"' in dash_row


def test_clear_failover_only_while_on(make_client):
    assert "Clear failover" not in make_client(failover=False).get("/").text
    html = make_client(failover=True).get("/").text
    assert "Clear failover" in html and "failover is on" in html


def test_restore_shows_newest_three_then_show_all(make_client):
    snaps = [{"name": f"snap-{i}", "path": f"/x/{i}", "kind": "usb"} for i in range(5)]
    html = make_client(snapshots=snaps).get("/").text
    assert "Show all backups (5)" in html
    assert html.index("snap-2") < html.index("Show all backups") < html.index("snap-3")
    # Restore sits below Services now.
    assert html.index("<h2>Services</h2>") < html.index("Restore from backup")


def test_recover_password_session_gets_header_without_switcher(make_client):
    html = make_client().get("/").text
    assert 'class="icon-btn sign-out"' not in html  # the old sign-out icon is gone
    assert 'aria-label="Account: stonepi"' in html  # avatar menu with Log out
    assert 'title="Dashboard"' in html  # stands in for the switcher's Home
    assert 'aria-label="Switch app"' not in html


def test_logs_picker_rejects_unknown_units(make_client):
    client = make_client({"stonepi-pinboard": ("failed", "enabled")})
    calls: list = []
    real = recover._run

    def spy(cmd, timeout=30):
        calls.append(cmd)
        return real(cmd, timeout)

    recover._run = spy
    try:
        assert "Showing: Pinboard" in client.get("/logs").text  # default: the failed ones
        assert "Showing: Library" in client.get("/logs?unit=stonepi-library").text
        client.get("/logs?unit=sshd")
    finally:
        recover._run = real
    journals = [c for c in calls if c[0] == "journalctl"]
    assert ["-u", "sshd"] not in [c[i : i + 2] for c in journals for i in range(len(c))]


def test_fallback_header_when_shared_packages_missing():
    from jinja2 import ChoiceLoader, Environment, FileSystemLoader

    env = Environment(
        loader=ChoiceLoader(
            [
                FileSystemLoader(str(recover.APP_DIR / "templates")),
                FileSystemLoader(str(recover.APP_DIR / "templates_fallback")),
            ]
        )
    )
    page = env.from_string(
        '{% from "_topbar.html" import topbar with context %}{{ topbar("x") }}'
    ).render(header_state=None, header_name="stonepi", csrf_token="t", portal_home="/")
    assert '<form method="post" action="logout"' in page and "Log out" in page
    assert 'title="Dashboard"' in page
