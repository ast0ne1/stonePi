"""Projects belong to the account that made them; admins see and manage every project.

Another member's project is treated exactly like a missing one, and the guard test fails
if a new "act on project" route is added without that check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stonepi_auth.session import COOKIE_NAME, encode_session

from tests.test_build_output import GAME_REPLY

SECRET = "studio-owner-secret"
ALICE = "a11ce000-0000-4000-8000-000000000001"
BOB = "b0b00000-0000-4000-8000-000000000002"
ADMIN = "ad000000-0000-4000-8000-000000000003"

MEMBER_CAPS = {"studio": {"can_use_llm": True, "can_publish": True}}


def _cookie(user_id: str, name: str, *, admin: bool = False) -> str:
    return encode_session(
        secret=SECRET,
        user_id=user_id,
        username=name.lower(),
        display_name=name,
        is_admin=admin,
        apps=["studio", "fileserve"],
        session_id=f"s-{user_id[:4]}",
        permissions=MEMBER_CAPS,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import llm, routes, store

    monkeypatch.setattr(store, "STORE", tmp_path / "studio.json")
    monkeypatch.setattr(store, "WORKSPACE_DIR", tmp_path / "ws")
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "csrf_ok", lambda *_a: True)
    monkeypatch.setattr(routes, "bell_context", lambda *_a, **_k: {"show": False})

    def fake_stream(*_a, **_k):
        yield "text", GAME_REPLY
        yield "stop", "end_turn"

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    from app.main import app

    return TestClient(app), store


def _as(client: TestClient, user_id: str, name: str, *, admin: bool = False) -> TestClient:
    client.cookies.set(COOKIE_NAME, _cookie(user_id, name, admin=admin))
    return client


def _make(client, store, user_id, name, project_name, *, admin=False):
    _as(client, user_id, name, admin=admin)
    resp = client.post("/create", data={"name": project_name, "kind": "game"}, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_new_project_records_its_owner(env):
    client, store = env
    pid = _make(client, store, ALICE, "Alice", "Comet")
    project = store.get_project(pid)
    assert project["owner_id"] == ALICE
    assert project["owner_name"] == "Alice"


def test_member_list_shows_only_their_projects(env):
    client, store = env
    _make(client, store, ALICE, "Alice", "Alice game")
    _make(client, store, BOB, "Bob", "Bob game")
    page = _as(client, BOB, "Bob").get("/projects").text
    assert "Bob game" in page
    assert "Alice game" not in page
    assert "All projects" not in page  # members get no All tab


def test_member_all_view_param_is_ignored(env):
    client, store = env
    _make(client, store, ALICE, "Alice", "Alice game")
    page = _as(client, BOB, "Bob").get("/projects?view=all").text
    assert "Alice game" not in page


@pytest.mark.parametrize(
    "method,path,data",
    [
        ("get", "/p/{id}", None),
        ("get", "/p/{id}/preview/", None),
        ("post", "/p/{id}/chat", {"message": "make it blue", "intent": "build"}),
        ("post", "/p/{id}/rename", {"name": "Mine now"}),
        ("post", "/p/{id}/delete", {}),
        ("post", "/p/{id}/publish", {"title": "Mine"}),
    ],
)
def test_member_cannot_touch_anothers_project(env, method, path, data):
    client, store = env
    pid = _make(client, store, ALICE, "Alice", "Alice game")
    _as(client, BOB, "Bob")
    url = path.replace("{id}", pid)
    resp = getattr(client, method)(url, **({"data": data} if data is not None else {}), follow_redirects=False)
    if resp.status_code == 303:
        assert "Project+not+found" in resp.headers["location"]
    else:
        assert resp.status_code == 404
    project = store.get_project(pid)
    assert project is not None and project["name"] == "Alice game"
    assert not [m for m in project.get("messages") or [] if m.get("content") == "make it blue"]


def test_every_project_route_is_covered():
    """Adding a /p/{project_id}/… route without listing it above should fail loudly."""
    from app import routes

    covered = {"/p/{project_id}", "/p/{project_id}/preview/", "/p/{project_id}/preview/{asset_path:path}",
               "/p/{project_id}/chat", "/p/{project_id}/rename", "/p/{project_id}/delete",
               "/p/{project_id}/publish"}
    found = {r.path for r in routes.router.routes if "{project_id}" in getattr(r, "path", "")}
    assert found <= covered, f"New project route(s) need an ownership test: {sorted(found - covered)}"


def test_owner_can_rename_and_delete(env):
    client, store = env
    pid = _make(client, store, ALICE, "Alice", "Alice game")
    client.post(f"/p/{pid}/rename", data={"name": "Comet"}, follow_redirects=False)
    assert store.get_project(pid)["name"] == "Comet"
    resp = client.post(f"/p/{pid}/delete", data={}, follow_redirects=False)
    assert resp.headers["location"] == "/projects?msg=Project+deleted"
    assert store.get_project(pid) is None


def test_admin_all_view_and_filter(env):
    client, store = env
    _make(client, store, ALICE, "Alice", "Alice game")
    _make(client, store, BOB, "Bob", "Bob game")
    _as(client, ADMIN, "Adam", admin=True)
    mine = client.get("/projects").text
    assert "Alice game" not in mine and "All projects" in mine
    everyone = client.get("/projects?view=all").text
    assert "Alice game" in everyone and "Bob game" in everyone
    assert f"owner={ALICE}" in everyone and f"owner={BOB}" in everyone
    only_bob = client.get(f"/projects?view=all&owner={BOB}").text
    assert "Bob game" in only_bob and "Alice game" not in only_bob


def test_admin_can_manage_anothers_project(env):
    client, store = env
    pid = _make(client, store, ALICE, "Alice", "Alice game")
    _as(client, ADMIN, "Adam", admin=True)
    page = client.get(f"/p/{pid}")
    assert page.status_code == 200
    assert "Alice's" in page.text and "?view=all" in page.text
    resp = client.post(f"/p/{pid}/rename", data={"name": "Tidied", "back": "projects"}, follow_redirects=False)
    assert resp.headers["location"] == "/projects?view=all&msg=Renamed"
    resp = client.post(f"/p/{pid}/delete", data={}, follow_redirects=False)
    assert resp.headers["location"] == "/projects?view=all&msg=Project+deleted"
    assert store.get_project(pid) is None


def test_ownerless_projects_go_to_the_admin(env):
    client, store = env
    legacy = store.create_project("Old game", kind="game")  # made before ownership existed
    assert store.owner_id(legacy) == ""
    page = _as(client, ALICE, "Alice").get("/projects").text
    assert "Old game" not in page  # members never see unowned projects
    assert client.get(f"/p/{legacy['id']}").status_code == 404
    page = _as(client, ADMIN, "Adam", admin=True).get("/projects").text
    assert "Old game" in page  # now under the admin's own projects
    assert store.get_project(legacy["id"])["owner_id"] == ADMIN


def test_owner_label_follows_display_name(env):
    client, store = env
    pid = _make(client, store, ALICE, "Alice", "Alice game")
    client.cookies.set(COOKIE_NAME, encode_session(
        secret=SECRET, user_id=ALICE, username="alice", display_name="Alice Smith", is_admin=False,
        apps=["studio"], session_id="s2", permissions=MEMBER_CAPS,
    ))
    client.get("/projects")
    assert store.get_project(pid)["owner_name"] == "Alice Smith"
