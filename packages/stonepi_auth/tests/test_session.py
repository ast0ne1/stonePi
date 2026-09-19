from stonepi_auth.session import decode_session, encode_session, safe_next
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
    )
    user = decode_session(token, "secret")
    assert user is not None
    assert user.username == "adam"
    assert user.can_access("newscast")
    assert user.has_capability("newscast", "can_use_ntfy")
    assert not user.can_access("fileserve")
    assert decode_session(token, "other") is None


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


def test_strip_prefix():
    assert strip_prefix("/studio/", "/studio") == "/"
    assert strip_prefix("/studio", "/studio") == "/"
    assert strip_prefix("/studio/static/css/app.css", "/studio") == "/static/css/app.css"
    assert strip_prefix("/static/css/app.css", "/studio") == "/static/css/app.css"
    assert strip_prefix("/studio/p/abc/chat", "/studio") == "/p/abc/chat"
