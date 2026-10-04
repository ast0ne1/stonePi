from fastapi import FastAPI
from stonepi_auth.platform_lock import add_platform_lock
from stonepi_auth.brand import mount_brand_fonts
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR, env
import app.routes as _app_routes
from app.routes import router
from stonepi_auth.prefix import clean_prefix

app = FastAPI(title="StonePi Pinboard")
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


@app.on_event("startup")
def _start_reminder_alerts() -> None:
    # Alerts only exist under StonePi sign-in (Notify); standalone skips the tick.
    from app.alerts import start_scheduler
    from app.routes import _session_secret

    if _session_secret():
        start_scheduler()

# A platform install without a session secret locks instead of running solo.
platform_lock = add_platform_lock(app, lambda: _app_routes._session_secret())
