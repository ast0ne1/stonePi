"""Build-output parsing, site checks, and the streamed chat route."""

from __future__ import annotations

import json

from app import workspace
from stonepi_auth.session import PlatformUser

GAME_REPLY = """Here's a neon snake game. Arrows or WASD to steer.

<studio-file path="index.html">
<!doctype html>
<html><head><link rel="stylesheet" href="style.css"></head>
<body><canvas id="c"></canvas><script src="app.js"></script></body></html>
</studio-file>

<studio-file path="app.js">
const s = "quotes \\" and \\n newlines and </div> are fine";
function loop() { requestAnimationFrame(loop); }
</studio-file>
"""


def test_parse_file_blocks_keeps_raw_contents():
    files, unclosed = workspace.parse_file_blocks(GAME_REPLY)
    assert unclosed == []
    assert set(files) == {"index.html", "app.js"}
    assert 'quotes \\" and \\n newlines and </div>' in files["app.js"]
    assert files["index.html"].startswith("<!doctype html>")


def test_parse_file_blocks_flags_truncation():
    cut = GAME_REPLY.split("function loop")[0]
    files, unclosed = workspace.parse_file_blocks(cut)
    assert "index.html" in files
    assert unclosed == ["app.js"]


def test_parse_file_blocks_unwraps_stray_fences():
    reply = '<studio-file path="app.js">\n```js\nconsole.log(1);\n```\n</studio-file>'
    files, _ = workspace.parse_file_blocks(reply)
    assert files["app.js"] == "console.log(1);\n"


def test_parse_file_blocks_rejects_escaping_paths():
    files, _ = workspace.parse_file_blocks('<studio-file path="../evil.js">x</studio-file>')
    assert files == {}


def test_strip_file_blocks_leaves_prose():
    assert workspace.strip_file_blocks(GAME_REPLY) == "Here's a neon snake game. Arrows or WASD to steer."
    partial = GAME_REPLY.split("<studio-file")[0] + '<studio-file path="index.html">\n<!doc'
    assert "<!doc" not in workspace.strip_file_blocks(partial)


def test_check_site_reports_missing_and_absolute_refs(tmp_path):
    (tmp_path / "index.html").write_text(
        '<html><body><script src="app.js"></script><link href="/style.css" rel="stylesheet"></body></html>'
    )
    issues = workspace.check_site(tmp_path)
    assert any("app.js" in i and "does not exist" in i for i in issues)
    assert any("/style.css" in i and "relative" in i for i in issues)


def test_check_site_clean(tmp_path):
    files, _ = workspace.parse_file_blocks(GAME_REPLY)
    workspace.apply_file_map(tmp_path, files)
    (tmp_path / "style.css").write_text("body { margin: 0; }")
    assert workspace.check_site(tmp_path) == []


def _events(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def _client(tmp_path, monkeypatch, reply: str, stop: str = "end_turn"):
    from fastapi.testclient import TestClient

    from app import llm, routes, store

    monkeypatch.setattr(store, "STORE", tmp_path / "studio.json")
    monkeypatch.setattr(store, "WORKSPACE_DIR", tmp_path / "ws")
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "csrf_ok", lambda *_a: True)

    def fake_stream(*_a, **_k):
        for i in range(0, len(reply), 7):
            yield "text", reply[i : i + 7]
        yield "stop", stop

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    from app.main import app

    project = store.create_project("Snake", kind="game")
    return TestClient(app), project, store


def test_chat_build_streams_progress_and_applies(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    resp = client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"})
    events = _events(resp)
    assert events[0] == {"type": "status", "stage": "received"}
    done = events[-1]
    assert done["type"] == "done"
    assert done["applied"] == ["index.html", "app.js"]
    assert done["message"].startswith("Here's a neon snake game")
    root = store.workspace_root(project["id"])
    assert "requestAnimationFrame" in (root / "app.js").read_text()

    page = client.get(f"/p/{project['id']}/preview/")
    assert page.headers["cache-control"] == "no-store"
    assert 'source:"studio-preview"' in page.text
    assert page.text.index("studio-preview") < page.text.index("style.css")


def test_chat_build_truncated_changes_nothing(tmp_path, monkeypatch):
    cut = GAME_REPLY.split("function loop")[0]
    client, project, store = _client(tmp_path, monkeypatch, cut, stop="max_tokens")
    root = store.workspace_root(project["id"])
    before = (root / "index.html").read_text()
    events = _events(client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"}))
    done = events[-1]
    assert done["applied"] == []
    assert any("ran out of room" in i for i in done["issues"])
    assert (root / "index.html").read_text() == before


def test_intro_greets_user_by_first_name(tmp_path, monkeypatch):
    from app import routes

    client, project, _store = _client(tmp_path, monkeypatch, GAME_REPLY)
    user = PlatformUser(
        user_id="1", display_name="Maya Stone", username="maya", is_admin=True, apps=["studio"],
    )
    monkeypatch.setattr(routes, "_user", lambda _r: user)
    page = client.get(f"/p/{project['id']}").text
    assert "Hi Maya! Let&#39;s invent a game together." in page
    assert 'data-name="Maya"' in page

    monkeypatch.setattr(routes, "_user", lambda _r: None)
    assert "Hi! Let&#39;s invent a game together." in client.get(f"/p/{project['id']}").text


def test_unnamed_project_takes_title_from_first_build(tmp_path, monkeypatch):
    reply = GAME_REPLY.replace("<html><head>", "<html><head><title>Star Snake &amp; Friends</title>")
    client, _project, store = _client(tmp_path, monkeypatch, reply)
    project = store.create_project("", kind="game")
    assert project["name"] == "My new game" and project["auto_name"] is True
    done = _events(client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"}))[-1]
    assert done["renamed"] == "Star Snake & Friends"
    saved = store.get_project(project["id"])
    assert saved["name"] == "Star Snake & Friends" and saved["auto_name"] is False


def test_named_project_keeps_its_name(tmp_path, monkeypatch):
    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    done = _events(client.post(f"/p/{project['id']}/chat", data={"message": "snake", "intent": "build"}))[-1]
    assert done["renamed"] is None
    assert store.get_project(project["id"])["name"] == "Snake"


def test_missing_lookups_flags_js_that_expects_absent_elements():
    html = '<body><canvas id="game"></canvas><p class="score">Score <span data-score>0</span></p></body>'
    js = """
    const canvas = document.getElementById("game");
    const score = document.querySelector("[data-score]");
    const timer = document.querySelector("[data-timer]");
    const best = document.getElementById('best');
    const hud = document.querySelector('.score');
    const pad = document.querySelector('.controls');
    """
    assert workspace.missing_lookups(html, js) == ["[data-timer]", "#best", ".controls"]


def test_missing_lookups_ignores_elements_the_js_creates():
    html = "<body><main></main></body>"
    js = """
    const toast = document.createElement("div"); toast.id = "toast"; document.body.append(toast);
    const t = document.getElementById("toast");
    document.body.insertAdjacentHTML("beforeend", '<div class="overlay"></div>');
    const o = document.querySelector(".overlay");
    const inner = someEl.querySelector("span");
    const fancy = document.querySelector("main > .x");
    """
    assert workspace.missing_lookups(html, js) == []


def test_check_site_reports_missing_lookup(tmp_path):
    (tmp_path / "index.html").write_text('<html><body><script src="app.js"></script></body></html>')
    (tmp_path / "app.js").write_text('document.getElementById("game").getContext("2d");')
    issues = workspace.check_site(tmp_path)
    assert any("app.js looks for #game" in i for i in issues)


def test_build_bar_counts_changes_since_last_build(tmp_path, monkeypatch):
    from app import routes

    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    user = PlatformUser(user_id="1", display_name="Maya", username="maya", is_admin=True, apps=["studio"])
    monkeypatch.setattr(routes, "_user", lambda _r: user)
    pid = project["id"]
    page = client.get(f"/p/{pid}").text
    assert 'data-build-bar' in page and 'data-pending="0"' in page and "Build my game" in page

    client.post(f"/p/{pid}/chat", data={"message": "a snake", "intent": "clarify"})
    page = client.get(f"/p/{pid}").text
    assert 'data-pending="1"' in page and "Happy with the plan?" in page

    client.post(f"/p/{pid}/chat", data={"message": "build", "intent": "build"})
    page = client.get(f"/p/{pid}").text
    assert 'data-pending="0"' in page and "Rebuild with changes" in page

    client.post(f"/p/{pid}/chat", data={"message": "make it faster", "intent": "clarify"})
    client.post(f"/p/{pid}/chat", data={"message": "and pink", "intent": "clarify"})
    page = client.get(f"/p/{pid}").text
    assert 'data-pending="2"' in page and "asked for 2 changes" in page


def test_share_flow_live_link_party_and_update(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import routes

    client, project, store = _client(tmp_path, monkeypatch, GAME_REPLY)
    user = PlatformUser(user_id="1", display_name="Maya", username="maya", is_admin=True, apps=["studio"])
    monkeypatch.setattr(routes, "_user", lambda _r: user)
    monkeypatch.setattr(routes.env, "fileserve_public_url", "http://stonepi.local/files")

    class FakeFileServe:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None, files=None, headers=None):
            assert url.endswith("/api/studio/publish") and data["title"] == "Star Snake"
            return SimpleNamespace(
                status_code=200, headers={"content-type": "application/json"},
                json=lambda: {"ok": True, "page_id": 7, "public_path": "/u/maya/star-snake"},
            )

    monkeypatch.setattr(routes.httpx, "Client", FakeFileServe)
    pid = project["id"]
    client.post(f"/p/{pid}/chat", data={"message": "build", "intent": "build"})
    page = client.get(f"/p/{pid}").text
    assert "Share it with the family" in page and "Share my game" in page

    resp = client.post(f"/p/{pid}/publish", data={"title": "Star Snake", "description": "Eat stars!"},
                       follow_redirects=False)
    assert resp.status_code == 303 and "msg=Published" in resp.headers["location"]

    party = client.get(f"/p/{pid}?msg=Published").text
    assert "is live!" in party and "http://stonepi.local/files/u/maya/star-snake" in party
    assert 'class="banner banner-ok"' not in party  # the celebration replaces the plain banner

    live = client.get(f"/p/{pid}").text
    assert "It's live!" in live and "is live!" not in live
    assert "Eat stars!" in live  # description is remembered for the next share

    client.post(f"/p/{pid}/chat", data={"message": "rebuild", "intent": "build"})
    assert "Share the update" in client.get(f"/p/{pid}").text


def test_kids_without_publish_are_pointed_to_a_grown_up(tmp_path, monkeypatch):
    from app import routes

    client, project, _store = _client(tmp_path, monkeypatch, GAME_REPLY)
    kid = PlatformUser(user_id="2", display_name="Maya", username="maya", is_admin=False, apps=["studio"],
                       permissions={"studio": {"can_use_llm": True}})
    monkeypatch.setattr(routes, "_user", lambda _r: kid)
    client.post(f"/p/{project['id']}/chat", data={"message": "build", "intent": "build"})
    page = client.get(f"/p/{project['id']}").text
    assert "Ask a grown-up to share it" in page and "data-share-sheet" not in page
