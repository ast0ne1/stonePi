"""Outbound fetches must not reach loopback / private services; favicons can't carry script."""

from __future__ import annotations

import httpx
import pytest

from app.services import favicon, net_guard


def _resolve_to(monkeypatch, mapping: dict[str, str]):
    def fake(host, port, *a, **k):
        return [(2, 1, 6, "", (mapping.get(host, "93.184.216.34"), port))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8014/kiwix/",
        "http://localhost:8010/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "ftp://example.com/feed",
        "http://kiwix.localhost/",
    ],
)
def test_loopback_and_link_local_always_blocked(url, monkeypatch):
    monkeypatch.setenv("NEWSCAST_ALLOW_PRIVATE_FEEDS", "1")
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.check_url(url)


def test_dns_name_resolving_to_loopback_is_blocked(monkeypatch):
    _resolve_to(monkeypatch, {"sneaky.example": "127.0.0.1"})
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.check_url("https://sneaky.example/rss")


def test_private_ranges_need_opt_in(monkeypatch):
    monkeypatch.delenv("NEWSCAST_ALLOW_PRIVATE_FEEDS", raising=False)
    _resolve_to(monkeypatch, {"nas.lan": "192.168.1.20"})
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.check_url("http://nas.lan/feed.xml")
    with pytest.raises(net_guard.BlockedAddress):
        net_guard.check_url("http://10.0.0.5/feed.xml")
    monkeypatch.setenv("NEWSCAST_ALLOW_PRIVATE_FEEDS", "1")
    net_guard.check_url("http://nas.lan/feed.xml")
    net_guard.check_url("http://10.0.0.5/feed.xml")


def test_public_host_allowed(monkeypatch):
    _resolve_to(monkeypatch, {})
    net_guard.check_url("https://feeds.bbci.co.uk/news/rss.xml")


def test_redirect_hop_to_loopback_is_blocked(monkeypatch):
    _resolve_to(monkeypatch, {})
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8014/secret"})
        return httpx.Response(200, text="secret")

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks=net_guard.EVENT_HOOKS,
    ) as client:
        with pytest.raises(net_guard.BlockedAddress):
            client.get("http://public.example/feed")
    assert hits == ["http://public.example/feed"]


def test_blocked_address_is_an_httpx_error():
    # Existing fetch code catches httpx.HTTPError and treats it as a failed fetch.
    assert issubclass(net_guard.BlockedAddress, httpx.HTTPError)


def test_remote_svg_favicon_is_not_cached(tmp_path, monkeypatch):
    icons = tmp_path / "icons"
    icons.mkdir()
    monkeypatch.setattr(favicon, "FAVICON_DIR", icons)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert favicon._save(svg, "image/svg+xml", "https://evil.example/favicon.svg", ["https://evil.example/"]) is None
    assert list(icons.iterdir()) == []
    png = favicon._save(b"\x89PNG\r\n\x1a\nrest", "image/png", "https://ok.example/favicon.png", ["https://ok.example/"])
    assert png is not None and png.suffix == ".png"


def test_purge_keeps_bundled_svgs_and_their_aliases(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    runtime = tmp_path / "runtime"
    bundled.mkdir()
    runtime.mkdir()
    good = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    (bundled / "aeon.co.svg").write_bytes(good)
    (runtime / "aeon.co.svg").write_bytes(good)
    (runtime / "www.aeon.co.svg").write_bytes(good)  # alias copy of a bundled icon
    (runtime / "evil.example.svg").write_bytes(b"<svg><script>alert(1)</script></svg>")
    (runtime / "ok.example.png").write_bytes(b"\x89PNG\r\n\x1a\nrest")
    monkeypatch.setattr(favicon, "BUNDLED_FAVICON_DIR", bundled)
    monkeypatch.setattr(favicon, "FAVICON_DIR", runtime)
    assert favicon.purge_remote_svgs() == 1
    names = sorted(p.name for p in runtime.iterdir())
    assert names == ["aeon.co.svg", "ok.example.png", "www.aeon.co.svg"]


def test_favicons_served_with_locked_down_headers(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import FAVICON_CSP, FaviconFiles

    (tmp_path / "aeon.co.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    app = FastAPI()
    app.mount("/favicons", FaviconFiles(directory=str(tmp_path)), name="favicons")
    response = TestClient(app).get("/favicons/aeon.co.svg")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == FAVICON_CSP
    assert "sandbox" in FAVICON_CSP and "script-src" not in FAVICON_CSP
    assert response.headers["X-Content-Type-Options"] == "nosniff"
