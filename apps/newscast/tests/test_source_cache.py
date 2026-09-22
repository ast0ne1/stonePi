"""Feed body cache must revalidate — never serve a stale RSS forever."""

from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, SourceFetch
from app.services import source_cache


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_or_fetch_feed_revalidates_instead_of_serving_stale(monkeypatch, tmp_path: Path):
    db = _session()
    url = "https://example.com/rss.xml"
    monkeypatch.setattr(source_cache, "DATA_DIR", tmp_path)
    monkeypatch.setattr(source_cache, "FEED_CACHE_DIR", tmp_path / "feeds")
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, key):
            calls.append({"url": key})
            if len(calls) == 1:
                return _FakeResponse(200, text="<rss>old</rss>", headers={"etag": "v1"})
            return _FakeResponse(200, text="<rss>new</rss>", headers={"etag": "v2"})

    monkeypatch.setattr(source_cache.httpx, "Client", FakeClient)

    body1, code1 = source_cache.get_or_fetch_feed(db, url)
    assert code1 == 200 and body1 == "<rss>old</rss>"
    body2, code2 = source_cache.get_or_fetch_feed(db, url)
    assert code2 == 200 and body2 == "<rss>new</rss>"
    assert len(calls) == 2
    row = db.get(SourceFetch, url)
    assert row is not None and row.etag == "v2"


def test_get_or_fetch_feed_uses_304_cached_body(monkeypatch, tmp_path: Path):
    db = _session()
    url = "https://example.com/rss.xml"
    monkeypatch.setattr(source_cache, "DATA_DIR", tmp_path)
    monkeypatch.setattr(source_cache, "FEED_CACHE_DIR", tmp_path / "feeds")

    class FakeClient:
        def __init__(self, *a, **k):
            self.headers = k.get("headers") or {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, key):
            if self.headers.get("If-None-Match"):
                return _FakeResponse(304)
            return _FakeResponse(200, text="<rss>fresh</rss>", headers={"etag": '"abc"'})

    monkeypatch.setattr(source_cache.httpx, "Client", FakeClient)
    body1, _ = source_cache.get_or_fetch_feed(db, url)
    assert body1 == "<rss>fresh</rss>"
    body2, code2 = source_cache.get_or_fetch_feed(db, url)
    assert code2 == 200 and body2 == "<rss>fresh</rss>"
