from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import DATA_DIR, ROOT_DIR
from app.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app import display
    import stonepi_automations as automations
    from app import services

    try:
        from stonepi_vault import get_vault

        get_vault()
    except Exception:
        pass
    display.configure_display()
    display.start_scheduler()
    automations.configure(
        data_dir=DATA_DIR,
        push_display=lambda: display.push_overview(None),
        backup_info=services.backup_info,
        watch_evaluate=lambda: display.watch_snapshot(None),
    )
    automations.start_scheduler()
    yield


app = FastAPI(title="StonePi Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")
app.include_router(router)
