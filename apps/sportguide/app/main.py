from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from stonepi_auth.platform_lock import add_platform_lock
from stonepi_auth.brand import mount_brand_fonts
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import ROOT_DIR, env
import app.routes as _app_routes
from app.routes import router
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
        from app.services import schedule as schedule_service

        schedule_service.start_scheduler()
    except Exception:
        logger.exception("notify scheduler failed to start")
    yield
    try:
        from app.services import schedule as schedule_service

        schedule_service.stop_scheduler()
    except Exception:
        pass


app = FastAPI(title="StonePi SportGuide", lifespan=lifespan)
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
mount_brand_fonts(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
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

# A platform install without a session secret locks instead of running solo.
platform_lock = add_platform_lock(app, lambda: _app_routes._session_secret())
