from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from stonepi_auth.brand import mount_brand_fonts
from stonepi_auth.prefix import clean_prefix

from app import db
from app.config import ROOT_DIR, env
from app.routes import router
from app.services import catalog, content, downloads, kiwix

logger = logging.getLogger("library")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    if db.get_setting("setup_state") == "running":
        # Interrupted by a restart; the helper call is safe to repeat.
        db.set_setting("setup_state", "failed")
        db.set_setting("setup_error", "Setup was interrupted — run it again.")
    try:
        content.reconcile()
        content.write_manifest()
        kiwix.ensure_running()
    except Exception:
        logger.exception("reconcile failed")
    downloads.start()
    if catalog.cache_is_stale():
        content.refresh_catalog_in_background(db.get_setting("language", "en") or "en")
    yield
    downloads.stop()


app = FastAPI(title="StonePi Library", lifespan=lifespan)
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
