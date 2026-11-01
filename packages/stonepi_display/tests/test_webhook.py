from unittest.mock import patch

import httpx
import pytest

from stonepi_display.webhook import PinnedTransport, validate_webhook_url


def test_validate_webhook_url_requires_https():
    with pytest.raises(ValueError, match="https"):
        validate_webhook_url("http://trmnl.com/api/custom_plugins/x")


def test_validate_webhook_url_rejects_loopback_literal():
    with pytest.raises(ValueError, match="not allowed"):
        validate_webhook_url("https://127.0.0.1/hook")


@patch(
    "stonepi_display.webhook.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
)
def test_validate_webhook_url_accepts_public_https(_mock_gai):
    url = validate_webhook_url("https://example.com/api/custom_plugins/x")
    assert url.startswith("https://")


def test_validate_webhook_url_rejects_private_lan():
    with pytest.raises(ValueError, match="not allowed"):
        validate_webhook_url("https://192.168.0.20/hook")
    with pytest.raises(ValueError, match="not allowed"):
        validate_webhook_url("https://10.1.1.1/hook")


@patch(
    "stonepi_display.webhook.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("10.1.1.1", 443))],
)
def test_validate_webhook_url_rejects_name_that_resolves_private(_mock_gai):
    with pytest.raises(ValueError, match="blocked"):
        validate_webhook_url("https://example.com/hook")


def test_pinned_transport_connects_to_the_checked_address():
    seen: dict[str, str] = {}

    class Inner(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["host"] = request.headers["host"]
            seen["sni"] = str(request.extensions.get("sni_hostname") or "")
            return httpx.Response(204)

        def close(self) -> None:
            return None

    with httpx.Client(transport=PinnedTransport("93.184.216.34", Inner())) as client:
        response = client.post("https://example.com/hook", json={"ok": True})
    assert response.status_code == 204
    assert seen["url"].startswith("https://93.184.216.34/")
    assert seen["host"] == "example.com"
    assert seen["sni"] == "example.com"
