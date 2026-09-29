"""Boot stonepi_display + stonepi_notify for the Notify app."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.collect import collect_overview
from app.config import DATA_DIR

logger = logging.getLogger("notify.boot")


def _vault_webhook() -> str:
    try:
        from stonepi_vault import get_secret

        return get_secret("DISPLAY_WEBHOOK_URL", env_name="STONEPI_DISPLAY_WEBHOOK", default="") or ""
    except Exception:
        return ""


def _vault_ntfy_token() -> str:
    try:
        from stonepi_vault import get_secret

        return get_secret("STONEPI_NTFY_TOKEN", env_name="STONEPI_NTFY_TOKEN", default="") or ""
    except Exception:
        return ""


def _apply_migration_marker(data_dir: Path) -> None:
    """Oldest installs: TRMNL settings from Dashboard display.json → Destinations.

    ``_migrate_trmnl_destination`` then moves them onto the Display.
    """
    marker = data_dir / "migration_trmnl.json"
    if not marker.exists():
        return
    try:
        import stonepi_notify

        data = json.loads(marker.read_text(encoding="utf-8"))
        trmnl = data.get("trmnl") if isinstance(data, dict) else None
        if isinstance(trmnl, dict):
            stonepi_notify.save_destinations({"trmnl": trmnl})
        marker.rename(data_dir / "migration_trmnl.done.json")
    except Exception:
        logger.exception("TRMNL migration import failed")


_overview_cache: dict = {"at": 0.0, "value": None}


def cached_overview(max_age: float = 60.0) -> dict:
    """Collected merge_variables, reused for a minute (preview + pushes in one tick)."""
    import time

    now = time.monotonic()
    if _overview_cache["value"] is None or now - _overview_cache["at"] > max_age:
        _overview_cache["value"] = collect_overview()
        _overview_cache["at"] = now
    return dict(_overview_cache["value"])


def _record(results: list[dict]) -> None:
    import stonepi_notify

    for result in results:
        stonepi_notify.append_history(
            channel="trmnl",
            ok=bool(result.get("ok")),
            title=f"Display {result.get('name') or result.get('display_id')}",
            message=str(result.get("message") or ""),
            source="notify",
            event_id="trmnl.push",
        )


def push_due_displays() -> dict:
    """Scheduler tick: push each Display whose interval has elapsed."""
    import stonepi_display

    results = stonepi_display.push_due(lambda: cached_overview(max_age=30.0))
    _record(results)
    return {"ok": all(r.get("ok") for r in results), "results": results}


def push_one_display(display_id: str) -> dict:
    import stonepi_display

    display = stonepi_display.get_display(display_id) or {}
    result = stonepi_display.push_display(display_id, cached_overview(max_age=30.0))
    _record([{"display_id": display_id, "name": display.get("name"), **result}])
    return result


def _push_trmnl() -> dict:
    """Push every Display with TRMNL on (Dashboard automations, /api/push-trmnl)."""
    import stonepi_display

    results = stonepi_display.push_due(lambda: cached_overview(max_age=30.0), force=True)
    _record(results)
    if not results:
        return {"ok": False, "message": "No Display has TRMNL push switched on with a webhook."}
    ok = all(r.get("ok") for r in results)
    message = "; ".join(f"{r.get('name')}: {r.get('message')}" for r in results)
    return {"ok": ok, "message": message[:240], "results": results}


def _migrate_trmnl_destination() -> None:
    """Move the old single TRMNL destination onto the Display it pushed.

    Before multi-Display, Destinations held one webhook + interval and a
    "Display to push". That Display now owns them; its plugin still holds the
    old pasted markup, so it starts in "legacy" template mode.
    """
    import stonepi_display
    import stonepi_notify

    dest = stonepi_notify.load_destinations()
    trmnl = dest.get("trmnl") or {}
    if trmnl.get("migrated_to_display"):
        return
    display_id = str(trmnl.get("display_id") or stonepi_display.BUILTIN_DASHBOARD_ID)
    display = stonepi_display.get_display(display_id) or stonepi_display.get_display(
        stonepi_display.BUILTIN_DASHBOARD_ID
    )
    webhook = str(trmnl.get("webhook_url") or "").strip()
    if display and (webhook or trmnl.get("enabled")):
        try:
            if webhook and not stonepi_display.get_webhook(display["id"]):
                stonepi_display.set_webhook(display["id"], webhook)
        except Exception:
            logger.exception("Could not move the TRMNL webhook into the vault")
            return
        display["device"] = "v2" if trmnl.get("device") == "v2" else "og"
        display["trmnl"] = {
            **display.get("trmnl", {}),
            "enabled": bool(trmnl.get("enabled")),
            "interval_minutes": trmnl.get("interval_minutes") or 30,
            "template": "legacy",
            "last_push_at": trmnl.get("last_push_at"),
            "last_push_ok": trmnl.get("last_push_ok"),
            "last_push_message": trmnl.get("last_push_message"),
            "last_push_status": trmnl.get("last_push_status"),
        }
        stonepi_display.upsert_display(display)
        logger.info("Moved TRMNL destination onto Display %s", display["id"])
    stonepi_notify.save_destinations(
        {"trmnl": {**trmnl, "enabled": False, "webhook_url": "", "migrated_to_display": display_id}}
    )


def configure_all() -> None:
    data_dir = Path(DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    import stonepi_display
    import stonepi_notify

    from app.people import load_people

    stonepi_display.configure_displays(data_dir)
    # Ensure displays.json exists (triggers legacy migration)
    stonepi_display.load_displays()

    stonepi_notify.configure(
        data_dir=data_dir,
        vault_token_fn=_vault_ntfy_token,
        webhook_url_fn=_vault_webhook,
        push_trmnl_fn=push_due_displays,
        people_fn=load_people,
    )
    _apply_migration_marker(data_dir)
    try:
        _migrate_trmnl_destination()
    except Exception:
        logger.exception("TRMNL destination migration failed")
    prefs = stonepi_notify.load_prefs()
    if not (prefs.get("events") or {}):
        stonepi_notify.save_prefs(
            {
                "events": {
                    "pricewatch.target_reached": True,
                    "pricewatch.price_drop": True,
                    "newscast.publication_available": True,
                    "newscast.push_available": True,
                    "fileserve.publication_created": True,
                    "eventtrakr.event_approaching": True,
                    "sportguide.watched_match_approaching": True,
                    "pricescout.publication_released": True,
                    "studio.site_published": True,
                    "system.disk_warning": True,
                }
            }
        )
    stonepi_notify.start_scheduler()
    _start_queue_replay()


def _replay_queue() -> None:
    """Ingest events apps queued while Notify was down — locally, not via HTTP."""
    try:
        import stonepi_notify
        from stonepi_contracts import drain_emit_retry_queue

        def _ingest(payload: dict) -> bool:
            stonepi_notify.ingest_event(payload)  # never raises; invalid events are dropped
            return True

        drained = drain_emit_retry_queue(deliver=_ingest, limit=500)
        if drained:
            logger.info("Replayed %s queued event(s)", drained)
    except Exception:  # noqa: BLE001
        logger.exception("emit retry replay failed")


def _start_queue_replay() -> None:
    # Off the import path so a big queue never delays Notify starting to listen.
    import threading

    timer = threading.Timer(5.0, _replay_queue)
    timer.daemon = True
    timer.start()
