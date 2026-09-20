from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR, env
from app import db
from app.routes import router
from app.services import ingest
from stonepi_auth.prefix import clean_prefix

logger = logging.getLogger("pricescout")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    from app.services import favicon

    favicon.ensure_dir()
    favicon.capture_all_async()
    try:
        db.recategorize_products()
    except Exception:
        logger.exception("recategorize failed")
    if env.mock:
        db.seed_mock_if_empty()
    else:
        # Background first refresh so UI has data soon after start
        import threading

        def _boot_refresh() -> None:
            try:
                ingest.refresh_all()
                db.recategorize_products()
            except Exception:
                logger.exception("boot refresh failed")
                db.seed_mock_if_empty()

        threading.Thread(target=_boot_refresh, daemon=True).start()
    yield


app = FastAPI(title="StonePi PriceScout", lifespan=lifespan)
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")

# Favicons live under the data dir (writable); mount after ensure_dir in lifespan too.
from app.services import favicon as _favicon

_favicon.ensure_dir()
app.mount("/favicons", StaticFiles(directory=str(_favicon.FAVICON_DIR)), name="favicons")
if _prefix:
    app.mount(f"{_prefix}/favicons", StaticFiles(directory=str(_favicon.FAVICON_DIR)), name="favicons_prefixed")
app.include_router(router)
