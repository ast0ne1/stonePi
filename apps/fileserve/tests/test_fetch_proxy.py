from unittest.mock import patch

from app.services import fetch_proxy


def test_validate_fetch_url_accepts_https():
    assert fetch_proxy.validate_fetch_url("https://example.com/path").startswith("https://")


def test_validate_fetch_url_rejects_file_scheme():
    try:
        fetch_proxy.validate_fetch_url("file:///tmp/x")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "http" in str(exc).lower()


def test_validate_fetch_url_rejects_loopback_literal_on_lan():
    with patch.object(fetch_proxy, "is_public_exposure", return_value=False):
        try:
            fetch_proxy.validate_fetch_url("http://127.0.0.1/x")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "not allowed" in str(exc).lower()


@patch("app.services.fetch_proxy.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.10", 80))])
def test_validate_fetch_url_allows_rfc1918_on_lan(_mock_gai):
    with patch.object(fetch_proxy, "is_public_exposure", return_value=False):
        assert fetch_proxy.validate_fetch_url("http://192.168.1.10/x").startswith("http://")


@patch("app.services.fetch_proxy.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.10", 80))])
def test_validate_fetch_url_blocks_rfc1918_when_public(_mock_gai):
    with patch.object(fetch_proxy, "is_public_exposure", return_value=True):
        try:
            fetch_proxy.validate_fetch_url("http://192.168.1.10/x")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "blocked" in str(exc).lower()


@patch("app.services.fetch_proxy.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 8014))])
def test_validate_fetch_url_blocks_dns_name_resolving_to_loopback(_mock_gai):
    with patch.object(fetch_proxy, "is_public_exposure", return_value=False):
        try:
            fetch_proxy.validate_fetch_url("http://sneaky.example/x")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "blocked" in str(exc).lower()


def test_fetch_url_revalidates_redirect_hops(monkeypatch):
    import httpx

    def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8014/kiwix"})
        return httpx.Response(200, content=b"secret")

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(fetch_proxy.httpx, "Client", fake_client)
    monkeypatch.setattr(
        fetch_proxy.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))],
    )
    try:
        fetch_proxy.fetch_url("http://public.example/start")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "not allowed" in str(exc).lower() or "blocked" in str(exc).lower()
