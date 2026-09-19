from unittest.mock import patch

import pytest

from stonepi_display.webhook import validate_webhook_url


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
