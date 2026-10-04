from stonepi_auth.session import (
    decode_session,
    encode_session,
    factory_admin_warning,
    safe_next,
)
from stonepi_auth.prefix import prefix_path, rewrite_text, strip_prefix


def test_round_trip_session():
    token = encode_session(
        secret="secret",
        user_id="abc",
        username="adam",
        display_name="Adam",
        is_admin=True,
        apps=["newscast", "dashboard"],
        session_id="sid-1",
        permissions={"newscast": {"can_use_ntfy": True}},
        using_factory_admin=True,
    )
    user = decode_session(token, "secret")
    assert user is not None
    assert user.username == "adam"
    assert user.can_access("newscast")
    assert user.has_capability("newscast", "can_use_ntfy")
    assert not user.can_access("fileserve")
    assert user.using_factory_admin is True
    assert factory_admin_warning(user) is True
    assert decode_session(token, "other") is None


def test_factory_flag_requires_admin():
    token = encode_session(
        secret="secret",
        user_id="u1",
        username="jane",
        display_name="Jane",
        is_admin=False,
        apps=["pinboard"],
        session_id="sid-2",
        using_factory_admin=True,
    )
    user = decode_session(token, "secret")
    assert user is not None
    assert user.using_factory_admin is False
    assert factory_admin_warning(user) is False


def test_safe_next():
    assert safe_next("/news/") == "/news/"
    assert safe_next("http://evil.example/") == "/"
    assert safe_next("http://127.0.0.1:8001/").startswith("http://127.0.0.1:8001")
    assert safe_next("http://stonepi.home/") == "http://stonepi.home/"
    assert safe_next("http://stonepi.local/") == "http://stonepi.local/"
    assert safe_next("http://192.168.0.225/") == "http://192.168.0.225/"


def test_prefix_rewrite():
    html = '<a href="/login">x</a><link href="/static/app.css">'
    out = rewrite_text(html, "/news")
    assert 'href="/news/login"' in out
    assert 'href="/news/static/app.css"' in out
    assert prefix_path("/auth/login", "/news") == "/auth/login"
    js = 'send("/api/ingest/status"); fetch("/api/ollama/models")'
    rewritten = rewrite_text(js, "/news")
    assert 'send("/news/api/ingest/status")' in rewritten
    assert 'fetch("/news/api/ollama/models")' in rewritten


def test_strip_prefix():
    assert strip_prefix("/studio/", "/studio") == "/"
    assert strip_prefix("/studio", "/studio") == "/"
    assert strip_prefix("/studio/static/css/app.css", "/studio") == "/static/css/app.css"
    assert strip_prefix("/static/css/app.css", "/studio") == "/static/css/app.css"
    assert strip_prefix("/studio/p/abc/chat", "/studio") == "/p/abc/chat"


def test_prefix_middleware_serves_routes_and_static_both_ways(tmp_path):
    """Direct hits with the prefix (run-dev, no nginx) and stripped hits (nginx) both work."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
    from starlette.testclient import TestClient

    from stonepi_auth.prefix import PrefixMiddleware

    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "app.css").write_text("body{}")
    app = Starlette(
        routes=[
            Route("/healthz", lambda request: PlainTextResponse("ok")),
            Mount("/static", StaticFiles(directory=str(tmp_path)), name="static"),
        ]
    )
    app.add_middleware(PrefixMiddleware, prefix="/notify")
    client = TestClient(app)
    for path in ("/notify/static/css/app.css", "/static/css/app.css", "/notify/healthz", "/healthz"):
        assert client.get(path).status_code == 200, path
    assert client.get("/notify/static/css/missing.css").status_code == 404


def test_every_catalog_app_path_is_left_alone_by_other_prefixes():
    """Auth's post-sign-in redirect to /library/read/... must not become /auth/library/..."""
    from stonepi_auth.catalog import APP_CATALOG

    assert prefix_path("/library/read/content/knots/guide.pdf", "/auth") == "/library/read/content/knots/guide.pdf"
    for item in APP_CATALOG:
        root = (item.get("path") or "/").rstrip("/")
        if not root:
            continue  # Dashboard lives at /
        assert prefix_path(root + "/x", "/auth") == root + "/x", item["id"]


def test_capability_missing_from_older_cookie_uses_catalog_default():
    from stonepi_auth.session import PlatformUser

    member = PlatformUser(user_id="2", username="kid", display_name="Kid", is_admin=False,
                          apps=["pinboard", "studio"], permissions={"pinboard": {"can_assign_others": False}})
    assert member.has_capability("pinboard", "can_post_notices") is True  # not in cookie: default on
    assert member.has_capability("pinboard", "can_assign_others") is False  # explicit choice wins
    assert member.has_capability("studio", "can_use_llm") is False  # default off
    assert member.has_capability("newscast", "can_add_custom_sources") is False  # no app access
