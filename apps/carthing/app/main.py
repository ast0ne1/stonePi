import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR
from app.connector import connector
from app.routes import router

# Without this the connector's INFO lines (connected / showing the panel / disconnected)
# never reach the journal, which left device drop-outs with no trail.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# The panel polls Notify every ~10 s; one line per HTTP request would bury the device events.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    connector.start()
    try:
        yield
    finally:
        # Service stop (or Notify switching the panel off): hand the device its own web app back.
        connector.stop()


# No prefix middleware: the device loads "/" through `adb reverse`, and Notify's preview
# proxy relies on the panel's relative URLs (<base href>) instead.
app = FastAPI(title="StonePi Car Thing", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Design-system fonts (Sora, IBM Plex Sans) for the panel, mounted before /static so
# panel.css can load them relatively (the device and Notify's preview both use <base href>).
_BRAND_FONTS = next(
    (p for p in (ROOT_DIR.parents[1] / "packages" / "stonepi_brand" / "fonts", Path("/opt/stonepi/packages/stonepi_brand/fonts")) if p.is_dir()),
    None,
)
if _BRAND_FONTS is not None:
    app.mount("/static/fonts", StaticFiles(directory=str(_BRAND_FONTS)), name="brand_fonts")
    # App icons for page widgets (static/brand/icons/apps/<id>.svg).
    if (_BRAND_FONTS.parent / "static").is_dir():
        app.mount("/static/brand", StaticFiles(directory=str(_BRAND_FONTS.parent / "static")), name="brand_static")
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")
app.include_router(router)
