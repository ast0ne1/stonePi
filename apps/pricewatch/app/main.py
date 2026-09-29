from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from stonepi_auth.brand import mount_brand_fonts
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import ROOT_DIR, env
from app.routes import router
from app.services import schedule
from stonepi_auth.prefix import clean_prefix

logger = logging.getLogger("pricewatch")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    if env.mock:
        db.ensure_mock_source(enabled=True)
    else:
        # Drop leftover mock from older installs when nothing depends on it.
        ok, _ = db.delete_source("mock")
        if ok:
            logger.info("Removed unused mock source")
    try:
        schedule.start_scheduler()
    except Exception:
        logger.exception("scheduler start failed")
    yield
    try:
        schedule.stop_scheduler()
    except Exception:
        logger.exception("scheduler stop failed")


app = FastAPI(title="StonePi PriceWatch", lifespan=lifespan)
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
mount_brand_fonts(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")
app.include_router(router)
