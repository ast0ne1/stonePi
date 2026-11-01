from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from stonepi_auth.brand import mount_brand_fonts
from stonepi_auth.platform_lock import add_platform_lock
from stonepi_auth.prefix import clean_prefix

from app import db
from app.services import dlna
from app.config import ROOT_DIR, env
import app.routes as _app_routes
from app.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    if env.mock:
        db.seed_mock_favorites_if_empty()
        dlna.refresh(force=True)  # the pretend speakers
    else:
        dlna.start_background_refresh()  # DLNA speakers on the home network, every 10 minutes
    yield


app = FastAPI(title="StonePi Radio", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
mount_brand_fonts(app)
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")
app.include_router(router)

platform_lock = add_platform_lock(app, lambda: _app_routes._session_secret())
