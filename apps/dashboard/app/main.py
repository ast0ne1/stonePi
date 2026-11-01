import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from stonepi_auth.brand import mount_brand_fonts
from fastapi.staticfiles import StaticFiles

from app.config import DATA_DIR, ROOT_DIR
from app.notifications import router as notifications_router
from app.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app import display
    from app import collector
    import stonepi_automations as automations
    from app import services

    try:
        from stonepi_vault import get_vault

        get_vault()
    except Exception:
        pass
    collector.start()

    # TRMNL pushes are per Display and owned by Notify.
    def _push_display():
        try:
            import httpx
            from stonepi_auth.internal import sign_internal_v2

            path = "/api/push-trmnl"
            body = b"{}"
            headers = {
                "Content-Type": "application/json",
                **sign_internal_v2(services.session_secret(), "POST", path, body),
            }
            with httpx.Client(timeout=30.0) as client:
                # JSON body + internal v2 signature (loopback only; nginx 404s this at the edge).
                response = client.post(
                    "http://127.0.0.1:8012/api/push-trmnl",
                    content=body,
                    headers=headers,
                )
                if response.status_code < 400:
                    return response.json() if response.content else {"ok": True}
                return {"ok": False, "message": f"Notify push failed (HTTP {response.status_code})."}
        except Exception:  # noqa: BLE001 - surfaced in the automation log
            logging.getLogger("dashboard").exception("notify push failed")
            return {"ok": False, "message": "Notify is not reachable."}

    automations.configure(
        data_dir=DATA_DIR,
        push_display=_push_display,
        backup_info=services.backup_info,
        # The collector already evaluates Watch every 20 s; reuse it instead of
        # probing every service again each minute.
        watch_evaluate=lambda: collector.get_snapshot().get("watch") or display.watch_snapshot(None),
    )
    automations.start_scheduler()
    yield
    collector.stop()


app = FastAPI(title="StonePi Dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "app" / "static")), name="static")
mount_brand_fonts(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
app.include_router(notifications_router)
app.include_router(router)
