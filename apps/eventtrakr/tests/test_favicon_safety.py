from app import create_app
from app.services import favicon


def test_svg_and_html_icons_are_not_cached():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert not favicon._looks_like_image(svg, "image/svg+xml")
    # A server lying about the type still doesn't get an SVG cached.
    assert not favicon._looks_like_image(svg, "image/png")
    assert not favicon._looks_like_image(b'<?xml version="1.0"?>' + svg, "")
    assert not favicon._looks_like_image(b"<!DOCTYPE html><html></html>", "image/x-icon")
    assert favicon._looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\0" * 64, "image/png")


def test_favicon_cache_is_served_sandboxed(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "evil.example.svg").write_bytes(b"<svg><script>alert(1)</script></svg>")
    res = create_app().test_client().get("/favicon-cache/evil.example.svg")
    assert res.status_code == 200
    assert "sandbox" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"


class _Row:
    def __init__(self, url, favicon_path=None, favicon_checked_at=None):
        self.url = url
        self.favicon_path = favicon_path
        self.favicon_checked_at = favicon_checked_at


class _Db:
    def commit(self):
        pass


def test_missing_or_svg_icons_fall_back_to_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    (tmp_path / "here.example.png").write_bytes(b"\x89PNG")
    (tmp_path / "old.example.svg").write_bytes(b"<svg/>")
    assert favicon.cached_src("here.example.png") == "/favicon-cache/here.example.png"
    assert favicon.cached_src("gone.example.ico") is None
    assert favicon.cached_src("old.example.svg") is None
    assert favicon.cached_src("../here.example.png") is None
    assert favicon.cached_src(None) is None


def test_backfill_heals_missing_files_despite_cooldown(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        name = f"{favicon.host_key(url)}.png"
        (tmp_path / name).write_bytes(b"\x89PNG")
        return name

    monkeypatch.setattr(favicon, "fetch_favicon", fake_fetch)
    # SQLite returns naive datetimes; "just checked" would normally block a refetch.
    just_now = datetime.now(timezone.utc).replace(tzinfo=None)
    lost = _Row("https://lost.example/events", "lost.example.ico", just_now)
    svg = _Row("https://jungle.example", "jungle.example.svg", just_now)
    cooling = _Row("https://none.example", None, just_now)
    (tmp_path / "jungle.example.svg").write_bytes(b"<svg/>")
    # A second row for an already-healed host reuses the file instead of refetching.
    same_host = _Row("https://www.lost.example/other", "lost.example.ico", just_now)

    assert favicon.needs_backfill(lost) and not favicon.needs_backfill(cooling)
    assert favicon.backfill(_Db(), [lost, svg, cooling, same_host]) == 3
    assert lost.favicon_path == "lost.example.png"
    assert svg.favicon_path == "jungle.example.png"
    assert same_host.favicon_path == "lost.example.png"
    assert cooling.favicon_path is None
    assert fetched == ["https://lost.example/events", "https://jungle.example"]
    assert not favicon.needs_backfill(lost)


def test_favicon_cache_serves_explicit_image_types(tmp_path, monkeypatch):
    monkeypatch.setattr(favicon, "FAVICON_DIR", tmp_path)
    client = create_app().test_client()
    for ext, mimetype in (("ico", "image/x-icon"), ("png", "image/png"), ("webp", "image/webp")):
        (tmp_path / f"site.example.{ext}").write_bytes(b"\0\0\1\0")
        res = client.get(f"/favicon-cache/site.example.{ext}")
        assert res.status_code == 200
        assert res.mimetype == mimetype
