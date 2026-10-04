from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR
from app.connector import connector
from app.routes import router


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
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")
app.include_router(router)
