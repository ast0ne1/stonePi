from fastapi import FastAPI
from stonepi_auth.platform_lock import add_platform_lock
from stonepi_auth.brand import mount_brand_fonts
from fastapi.staticfiles import StaticFiles

from app.boot import configure_all
from app.carthing_routes import router as carthing_router
from app.config import ROOT_DIR, env
from app.me_routes import router as me_router
from app.phone_alerts import router as phone_alerts_router
import app.routes as _app_routes
from app.routes import router
from stonepi_auth.prefix import clean_prefix

configure_all()

app = FastAPI(title="StonePi Notify")
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
mount_brand_fonts(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")
app.include_router(me_router)
# Before the main router: /displays/carthing must win over /displays/{display_id}.
app.include_router(carthing_router)
app.include_router(phone_alerts_router)
app.include_router(router)

# A platform install without a session secret locks instead of running solo.
platform_lock = add_platform_lock(app, lambda: _app_routes._session_secret())
