import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.errors import ApiError, api_error_response
from app.internal import router as internal_router
from app.pebble import router as pebble_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# No UI and no prefix middleware: nginx forwards /agent/… unchanged, and admins manage
# devices in Notify → Sidekick (through the signed internal API).
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the speech models in the background: /healthz answers at once, and the first ask
    # after a restart doesn't pay several seconds of loading.
    from app import speech

    threading.Thread(target=speech.warm_up, name="speech-warm-up", daemon=True).start()
    yield


app = FastAPI(title="StonePi AgentLink", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_response)


def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
    """Anything unplanned (e.g. SQLite "database is locked") still answers in PROTOCOL.md §3 shape."""
    logging.getLogger("agentlink").exception("Unhandled error", exc_info=exc)
    return JSONResponse({"error": "internal", "message": "Something went wrong"}, status_code=500)


app.add_exception_handler(Exception, _unexpected)
app.include_router(internal_router)
app.include_router(pebble_router)


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "agentlink", "version": __version__}


@app.get("/", include_in_schema=False)
def home():
    """No UI here: send people (Health's link, in run-dev by port) to Notify → Sidekick."""
    notify = (os.environ.get("STONEPI_NOTIFY_URL") or "").rstrip("/")
    return RedirectResponse(f"{notify}/sidekick" if notify else "/notify/sidekick", status_code=303)
