from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ROOT_DIR, env
from app.routes import router
from stonepi_auth.prefix import clean_prefix

app = FastAPI(title="StonePi Studio")
_prefix = clean_prefix(env.stonepi_prefix)
if _prefix:
    from stonepi_auth.prefix import PrefixMiddleware

    app.add_middleware(PrefixMiddleware, prefix=_prefix)

_static = str(ROOT_DIR / "app" / "static")
app.mount("/static", StaticFiles(directory=_static), name="static")
# When HTML rewrites assets to /{prefix}/static/... and the process is hit
# directly (no nginx strip), serve the same files under the prefixed path too.
if _prefix:
    app.mount(f"{_prefix}/static", StaticFiles(directory=_static), name="static_prefixed")
app.include_router(router)
