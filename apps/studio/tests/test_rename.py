"""Renaming a project from the editor header / Projects cards."""

from __future__ import annotations

from stonepi_auth.session import PlatformUser

from tests.test_build_output import GAME_REPLY, _client


def _unnamed(store):
    return store.create_project("", kind="game")


def test_rename_sets_name_and_redirects_back(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post(
        f"/p/{project['id']}/rename", data={"name": "  Star   Snake  "}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/p/{project['id']}?msg=Renamed"
    saved = store.get_project(project["id"])
    assert saved["name"] == "Star Snake"
    assert saved["auto_name"] is False


def test_rename_from_projects_list_returns_there(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post(
        f"/p/{project['id']}/rename",
        data={"name": "Comet", "back": "projects"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/projects?msg=Renamed"
    assert store.get_project(project["id"])["name"] == "Comet"
    page = client.get("/projects").text
    assert f'data-rename-action="/p/{project["id"]}/rename"' in page
    assert "data-rename-sheet" in page


def test_rename_caps_length(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    client.post(f"/p/{project['id']}/rename", data={"name": "x" * 200}, follow_redirects=False)
    assert store.get_project(project["id"])["name"] == "x" * store.NAME_MAX


def test_rename_rejects_empty_name(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post(f"/p/{project['id']}/rename", data={"name": "   "}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(f"/p/{project['id']}?err=")
    assert store.get_project(project["id"])["name"] == "Snake"
    page = client.get(resp.headers["location"]).text
    assert "Give your project a name first." in page


def test_rename_unknown_project(tmp_path, monkeypatch):
    client, _project, _store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post("/p/nope/rename", data={"name": "Hi"}, follow_redirects=False)
    assert resp.headers["location"] == "/projects?err=Project+not+found"


def test_rename_ignores_foreign_back_target(tmp_path, monkeypatch):
    client, project, _store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post(
        f"/p/{project['id']}/rename",
        data={"name": "Hi", "back": "https://evil.example/"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == f"/p/{project['id']}?msg=Renamed"


def test_rename_needs_studio_access(tmp_path, monkeypatch):
    from app import routes

    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    outsider = PlatformUser(user_id="2", display_name="Guest", username="guest", is_admin=False, apps=[])
    monkeypatch.setattr(routes, "_user", lambda _r: outsider)
    resp = client.post(f"/p/{project['id']}/rename", data={"name": "Mine now"}, follow_redirects=False)
    assert resp.status_code == 403
    assert store.get_project(project["id"])["name"] == "Snake"


def test_rename_rejects_expired_form(tmp_path, monkeypatch):
    from app import routes

    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    monkeypatch.setattr(routes, "csrf_ok", lambda *_a: False)
    resp = client.post(f"/p/{project['id']}/rename", data={"name": "Nope"}, follow_redirects=False)
    assert resp.headers["location"] == f"/p/{project['id']}?err=Form+expired"
    assert store.get_project(project["id"])["name"] == "Snake"


def test_editor_header_has_rename_button(tmp_path, monkeypatch):
    client, project, _store = _client(tmp_path, monkeypatch, GAME_REPLY)
    page = client.get(f"/p/{project['id']}").text
    assert 'aria-label="Rename project"' in page
    assert f'action="/p/{project["id"]}/rename"' in page


def test_manual_name_survives_first_build(tmp_path, monkeypatch):
    """An unnamed project the user renames before building keeps their name."""
    client, _project, store = _client(tmp_path, monkeypatch, GAME_REPLY.replace("<head>", "<head><title>Neon Snake</title>"))
    project = _unnamed(store)
    assert project["auto_name"] is True
    client.post(f"/p/{project['id']}/rename", data={"name": "Maya's Snake"}, follow_redirects=False)
    client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"})
    saved = store.get_project(project["id"])
    assert saved["built"] is True
    assert saved["name"] == "Maya's Snake"


def test_unnamed_project_still_adopts_build_title(tmp_path, monkeypatch):
    client, _project, store = _client(tmp_path, monkeypatch, GAME_REPLY.replace("<head>", "<head><title>Neon Snake</title>"))
    project = _unnamed(store)
    client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"})
    assert store.get_project(project["id"])["name"] == "Neon Snake"
    # adopt_name is one-shot: a later build (or direct call) can't overwrite a name.
    assert store.adopt_name(project["id"], "Other") is None
