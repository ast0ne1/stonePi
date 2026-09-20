from __future__ import annotations

from types import SimpleNamespace

from stonepi_auth.http import portal_home_url


class _Headers(dict):
    def get(self, key, default=None):
        for candidate in (key, key.lower(), key.title()):
            if candidate in self:
                return self[candidate]
        return default


def _request(*, host: str, scheme: str = "http"):
    return SimpleNamespace(
        headers=_Headers({"host": host}),
        url=SimpleNamespace(scheme=scheme, netloc=host),
        scheme=scheme,
    )


def test_portal_home_windows_uses_dashboard_fallback(monkeypatch):
    monkeypatch.setattr("stonepi_auth.http.os.name", "nt")
    req = _request(host="127.0.0.1:8005")
    assert portal_home_url(req, "http://127.0.0.1:8010") == "http://127.0.0.1:8010/"


def test_portal_home_path_install_keeps_request_host(monkeypatch):
    monkeypatch.setattr("stonepi_auth.http.os.name", "posix")
    req = _request(host="stonepi.home")
    assert portal_home_url(req, "http://127.0.0.1:8010") == "http://stonepi.home/"
