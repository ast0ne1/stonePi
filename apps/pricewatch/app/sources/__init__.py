from __future__ import annotations

from app.config import env
from app.sources.base import PriceSource
from app.sources.mock import MockSource
from app.sources.pricerunner import PriceRunnerDkSource


def get_source(source_id: str) -> PriceSource | None:
    if source_id == "mock" or (env.mock and source_id == "pricerunner_dk"):
        return MockSource()
    if source_id == "pricerunner_dk":
        return PriceRunnerDkSource()
    if source_id == "mock":
        return MockSource()
    return None


def enabled_sources(enabled_ids: list[str] | None = None) -> list[PriceSource]:
    ids = enabled_ids or ["pricerunner_dk"]
    out: list[PriceSource] = []
    for sid in ids:
        src = get_source(sid)
        if src is not None:
            out.append(src)
    if not out and env.mock:
        out.append(MockSource())
    return out
