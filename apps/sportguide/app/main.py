from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import ROOT_DIR, env
from app.routes import router
from app.services import ingest
from stonepi_auth.prefix import clean_prefix

logger = logging.getLogger("sportguide")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    try:
        from app.services import favicon

        favicon.ensure_dir()
        favicon.capture_all_async()
    except Exception:
        logger.exception("favicon bootstrap failed")
    try:
        ingest.maybe_daily_refresh(async_=True)
    except Exception:
        logger.exception("daily refresh check failed")
    yield


app = FastAPI(title="StonePi SportGuide", lifespan=lifespan)
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")

from app.services import favicon as _favicon  # noqa: E402

_favicon.ensure_dir()
app.mount("/favicons", StaticFiles(directory=str(_favicon.FAVICON_DIR)), name="favicons")
if _prefix:
    app.mount(
        f"{_prefix}/favicons",
        StaticFiles(directory=str(_favicon.FAVICON_DIR)),
        name="favicons_prefixed",
    )
app.include_router(router)
