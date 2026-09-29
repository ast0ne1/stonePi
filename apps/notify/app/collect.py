"""Scrape app /api/display endpoints and build TRMNL merge_variables."""

from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
import stonepi_display
from stonepi_auth import APP_CATALOG
from stonepi_display import variables as display_vars

from app.config import DATA_DIR

_APP_PORTS = {item["id"]: int(item["port"]) for item in APP_CATALOG}

SCRAPE_APPS = (
    "newscast",
    "eventtrakr",
    "pinboard",
    "fileserve",
    "auth",
    "studio",
    "pricescout",
    "sportguide",
    "pricewatch",
)


def _status_url(app_id: str) -> str:
    """Always probe loopback — avoids mDNS + nginx on every display fetch."""
    port = _APP_PORTS.get(app_id)
    return f"http://127.0.0.1:{port}/api/display" if port else ""


def _health_url(item: dict) -> str:
    return f"http://127.0.0.1:{item['port']}{item.get('health') or '/health'}"


def _fetch(url: str, client: httpx.Client) -> dict:
    if not url:
        return {}
    try:
        response = client.get(url)
        if response.status_code >= 400:
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _watch() -> dict:
    """Watch rollup; degrades to 'attention' if stonepi_watch isn't installed yet."""
    try:
        import stonepi_watch
    except ImportError:
        return {"level": "attention", "summary": "Watch unavailable", "reasons": [], "apps": []}
    return stonepi_watch.evaluate(
        health_url_for=_health_url,
        backup_info=_backup,
        data_dir=DATA_DIR,
        catalog=list(APP_CATALOG),
    )


def _backup() -> dict:
    try:
        from stonepi_watch import read_backup_info
    except ImportError:
        return {"status": "none"}
    return read_backup_info()


def collect_overview(cookies: dict[str, str] | None = None) -> dict[str, Any]:
    """Build merge_variables for the TRMNL Liquid blocks (see stonepi_display.variables)."""
    del cookies  # scheduled pushes have no session; Watch treats every app as enabled
    fetched: dict[str, dict] = {}
    with httpx.Client(timeout=1.0, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch, _status_url(app_id), client): app_id for app_id in SCRAPE_APPS}
            for fut in as_completed(futures):
                fetched[futures[fut]] = fut.result() or {}
    return stonepi_display.build_merge_variables(
        hostname=socket.gethostname(),
        watch=_watch(),
        backup=_backup(),
        fetched=fetched,
        cpu=display_vars.read_cpu_pct(),
        mem=display_vars.read_mem_pct(),
        temp=display_vars.read_temp_c(),
        uptime=display_vars.read_uptime(),
    )


def widget_payloads_for_display(
    display: dict[str, Any],
    *,
    overview: dict[str, Any] | None = None,
    destination_id: str = "stonepi_web",
) -> list[dict[str, Any]]:
    from stonepi_contracts import widget_by_id
    from stonepi_display import enrich_widget_for_destination

    overview = overview or collect_overview()
    rows = []
    for instance in display.get("widgets") or []:
        desc = widget_by_id(instance.get("widget_id") or "")
        if not desc:
            continue
        body = ""
        url = None
        wid = desc.id
        if wid == "stonepi.system_status":
            body = f"{overview.get('hostname')} · CPU {overview.get('cpu_disp')} · RAM {overview.get('mem_disp')}"
        elif wid == "stonepi.watch":
            body = str(overview.get("watch_summary") or "—")
        elif wid == "stonepi.apps":
            names = [a.get("n") for a in (overview.get("apps") or []) if isinstance(a, dict)]
            body = ", ".join(str(n) for n in names[:6]) or "—"
        elif wid == "stonepi.storage":
            body = f"Disk {overview.get('disk_disp')} · Backup {overview.get('backup_when')}"
        elif wid == "newscast.headlines":
            body = f"{overview.get('nc_feeds') or 0} feeds · {overview.get('nc_updated') or '—'}"
        elif wid == "eventtrakr.next_event":
            nxt = str(overview.get("et_next") or "")
            body = nxt if nxt and nxt != "None" else "—"
        elif wid == "fileserve.published_files":
            body = f"{overview.get('fs_pages') or 0} pages"
            origin = (os.environ.get("STONEPI_PUBLIC_ORIGIN") or "").rstrip("/")
            url = f"{origin}/files/" if origin else "/files/"
        elif wid == "pinboard.recent":
            lines = overview.get("pinboard_lines") or []
            body = " · ".join(str(x) for x in lines[:3]) or "—"
        elif wid == "pricewatch.watchlist":
            body = str(overview.get("pw_detail") or "—")
        elif wid == "sportguide.next_matches":
            body = str(overview.get("sg_detail") or "—")
        else:
            body = desc.blurb

        payload = {
            "instance_id": instance.get("id"),
            "widget_id": wid,
            "app": desc.app,
            "title": desc.label,
            "body": body,
            "size": instance.get("size") or "medium",
            "url": url,
            "action_label": "Open",
            "supports_qr": desc.supports_qr,
        }
        rows.append(enrich_widget_for_destination(payload, destination_id=destination_id))
    return rows
