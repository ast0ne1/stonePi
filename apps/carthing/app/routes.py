"""Car Thing panel: one shell page + small HTML fragments swapped in by panel.js.

Callers are either the paired device (token in ``?t=`` once, then the ``ct`` cookie)
or Notify's admin preview proxy (signed internal headers; shows the editor's draft).
Every URL in the HTML is relative, so the same pages work at ``/`` on the device and
under Notify's ``/carthing/panel/`` proxy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from stonepi_display import carthing

from app import __asset_rev__, __version__, music, radio, state
from app.config import ROOT_DIR

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, app_version=__version__)
router = APIRouter()
logger = logging.getLogger("carthing.panel")

TOKEN_COOKIE = "ct"
PREVIEW_HEADER = "x-carthing-preview"
BASE_HEADER = "x-carthing-base"
_BASE = re.compile(r"^/[A-Za-z0-9/_-]*/$")
MINI_LABELS = {a["id"]: a["label"] for a in carthing.MINI_APPS}
WIDGET_LABELS = {w["id"]: w["label"] for w in carthing.WIDGETS}
# Rows a large widget lists from its mini-app's feed (half: one column; full: two).
ITEMS_BY_SIZE = {"small": 2, "half": 4, "full": 8}
GRADIENT_CSS = {g["id"]: g["css"] for g in carthing.GRADIENTS}
# Web page snapshots (F14): a page widget fills the screen under the 68px strip, or all of
# it when the page's header auto-hides or is off ("page"); the screensaver fills it all. A
# widget's snapshot is refreshed every minute while it shows.
WEB_SIZES = {"widget": (800, 412), "page": (800, 480), "saver": (800, 480)}
WEB_WIDGET_EVERY_S = 60


class Caller:
    def __init__(self, kind: str, panel: dict | None, base: str = "/") -> None:
        self.kind = kind  # "device" | "preview" | "none" | "off"
        self.panel = panel or {}
        self.base = base

    @property
    def ok(self) -> bool:
        return self.kind in {"device", "preview"}

    @property
    def preview(self) -> bool:
        return self.kind == "preview"

    @property
    def config(self) -> dict:
        return self.panel.get("config") or carthing.default_config()


def _caller(request: Request) -> Caller:
    from stonepi_auth.internal import verify_internal

    headers = request.headers
    if headers.get(PREVIEW_HEADER) == "1":
        if verify_internal(state.session_secret(), request.method, request.url.path, headers):
            base = headers.get(BASE_HEADER) or "/"
            return Caller("preview", state.panel_state(draft=True), base if _BASE.match(base) else "/")
        return Caller("none", None)
    panel = state.panel_state()
    if not panel:
        return Caller("none", None)
    token = request.query_params.get("t") or request.cookies.get(TOKEN_COOKIE) or ""
    if not carthing.token_ok(token, str(panel.get("token_hash") or "")):
        return Caller("none", panel)
    if not panel.get("enabled"):
        return Caller("off", panel)
    state.device_seen.mark()
    return Caller("device", panel)


def _render(request: Request, template: str, caller: Caller, extra: dict | None = None, status: int = 200):
    ctx = {"caller": caller, "config": caller.config, "labels": MINI_LABELS}
    if extra:
        ctx.update(extra)
    return templates.TemplateResponse(request, template, ctx, status_code=status)


def _denied(request: Request, caller: Caller):
    return templates.TemplateResponse(
        request, "unpaired.html", {"caller": caller}, status_code=403 if caller.kind == "none" else 200
    )


def _clock_offset() -> dict[str, int]:
    now = datetime.now(timezone.utc)
    offset = now.astimezone().utcoffset()
    return {
        "now_ms": int(now.timestamp() * 1000),
        "tz_min": int(offset.total_seconds() // 60) if offset else 0,
    }


def _background_style(config: dict) -> str:
    bg = (config.get("idle") or {}).get("background") or {}
    kind, value = bg.get("type"), str(bg.get("value") or "")
    if kind == "solid":
        return f"background:{value}"
    if kind == "image":
        return f"background:#000 url('bg/{value}') center/cover no-repeat"
    return f"background:{GRADIENT_CSS.get(value) or GRADIENT_CSS['midnight']}"


def _page_index(config: dict, page: Any) -> int:
    """1-based ``?page=`` → 0-based index on this config's pages (out of range → first)."""
    try:
        number = int(page)
    except (TypeError, ValueError):
        return 0
    return number - 1 if 1 <= number <= len(config["pages"]) else 0


def _client_config(caller: Caller, page: int = 0) -> dict[str, Any]:
    """What panel.js needs. No secrets: the PIN is checked on the Pi."""
    cfg = caller.config
    return {
        "controls": cfg["controls"],
        "allowed": cfg["allowed_actions"],
        # Mini-apps in page order: what Next/Previous mini-app cycles through.
        "apps": [w for w in carthing.page_widget_ids(cfg) if w in MINI_LABELS],
        "pages": [p["name"] for p in cfg["pages"]],
        "page": page,
        "rotation": cfg["rotation"],
        "idle": cfg["idle"],
        "clock": cfg["clock"],
        "look": cfg["look"],
        "music": {
            "dial_volume": bool(_music_cfg(cfg)["dial_volume"]),
            "step": int(_music_cfg(cfg)["volume_step"]),
            "saver_artwork": bool(_music_cfg(cfg)["saver_artwork"]),
        },
        "radio": {
            "dial_volume": bool(_radio_cfg(cfg)["dial_volume"]),
            "step": int(_radio_cfg(cfg)["volume_step"]),
            "theme": str(_radio_cfg(cfg).get("theme") or "winamp"),
        },
        "rev": caller.panel.get("rev") or "",
        "preview": caller.preview,
        **_clock_offset(),
    }


def _widget(widget: dict, config: dict | None = None, web_names: dict[str, str] | None = None, full_h: bool = False) -> dict:
    """One widget on a page: its card, plus rows/numbers when it's large."""
    wid, size = widget["id"], widget.get("size") or "small"
    view: dict[str, Any] = {"id": wid, "size": size, "label": WIDGET_LABELS.get(wid, wid)}
    page_id = carthing.web_widget_page(wid)
    if page_id:
        names = web_names or {}
        return {
            **view,
            "kind": "web",
            "page_id": page_id,
            "label": names.get(page_id, "Web page"),
            "missing": page_id not in names,
            "every_s": WEB_WIDGET_EVERY_S,
            "size_key": "page" if full_h else "widget",  # the whole screen when there's no strip
        }
    if wid in ("clock", "weather", "time", "audio"):
        return {**view, "kind": wid}
    if wid == "music":
        np = _np(NP_AGE["widget"])
        cfg = config or carthing.default_config()
        if size == "full" and _music_cfg(cfg).get("look_from") == "radio" and np.get("connected"):
            # Look → Music → Spotify plays in Radio's theme: the Radio player, driving Spotify.
            return {**view, "kind": "music_skin", **_music_skin(cfg, np), "edge": full_h}
        # Full page with the header on Auto-hide or Off: Now Playing edge to edge, like the Music screen.
        return {**view, "kind": "music", "np": np, "opts": _music_options(cfg, np if size == "full" else None), "edge": size == "full" and full_h}
    if wid == "radio":
        cfg = config or carthing.default_config()
        now = radio.now_playing(NP_AGE["widget"], _radio_cfg(cfg)["output"])
        if size == "full" and _radio_cfg(cfg).get("look_from") == "music":
            # Look → Radio → Radio plays in Now Playing: Music's Now Playing, driving Radio.
            return {**view, "kind": "radio_np", "np": _radio_np(cfg, now), "opts": _radio_np_options(cfg, now), "edge": full_h}
        return {
            **view,
            "kind": "radio",
            "now": now,
            "opts": _radio_options(cfg, now),
            "edge": size == "full" and full_h,
        }
    data = state.feed(wid)
    card = data.get("card") or {}
    view.update(
        kind="system" if wid == "system" else "app",
        headline=str(card.get("headline") or ("—" if data.get("ok") else "Unavailable"))[:80],
        sub=str(card.get("sub") or "")[:80],
        badge=str(card.get("badge") or "")[:12],
        level=str(card.get("level") or ""),
        ok=bool(data.get("ok")),
    )
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    if wid == "system":
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        items = _system_items(items)
        down = [i for i in items if i.get("level") == "down"]
        view.update(stats=stats, services=items[:16], down=down)
    else:
        view["items"] = items[: ITEMS_BY_SIZE.get(size, 0)]
    return view


def _page_dots(cfg: dict) -> list[dict[str, Any]]:
    return [
        {"number": n, "name": p["name"], "allowed": f"page:{n}" in cfg["allowed_actions"]}
        for n, p in enumerate(cfg["pages"], start=1)
    ]


def _page_view(caller: Caller, index: int) -> dict[str, Any]:
    """Context for home.html: one page, laid out, with its page dots."""
    cfg = caller.config
    pages = cfg["pages"]
    index = index if 0 <= index < len(pages) else 0
    page = pages[index]
    layout = carthing.page_layout(page["widgets"])
    header = carthing.page_header(cfg, page)
    full_h = header["mode"] != "always"  # the page gets the whole screen
    web_names = state.web_page_names(caller.panel)
    cols = [[_widget(w, cfg, web_names, full_h) for w in col] for col in layout["cols"]]
    kind = layout["kind"]
    if kind == "full" and cols and cols[0] and cols[0][0]["kind"] == "web":
        kind = "web"  # edge to edge under the strip
    elif kind == "full" and cols and cols[0] and cols[0][0].get("kind") == "audio":
        kind = "audio"  # Spotify / Radio launcher edge to edge under the strip
    elif kind == "full" and cols and cols[0] and cols[0][0].get("edge") and cols[0][0].get("kind") in ("radio", "music_skin"):
        kind = "radio"  # Radio player edge to edge (Radio, or Spotify in Radio's theme)
    elif kind == "full" and cols and cols[0] and cols[0][0].get("edge"):
        kind = "np"  # Now Playing edge to edge (the header is Auto-hide or Off)
    return {
        "page": {"index": index, "number": index + 1, "name": page["name"], "count": len(pages)},
        "dots": _page_dots(cfg),
        "layout": {"kind": kind, "cols": cols, "count": sum(len(c) for c in cols)},
        # The strip: the page's own header settings, else the config's default.
        "header": header,
        # A clock on the page: the strip shows the page name instead of the time.
        "has_clock": any(w["id"] in ("clock", "time") for w in page["widgets"]),
        # Weather on the page already: the strip leaves it out.
        "has_forecast": any(w["id"] in ("clock", "weather") for w in page["widgets"]),
        "has_weather": any(w["id"] == "weather" for w in page["widgets"]),
        "weather": state.weather(cfg),
    }


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "carthing"}


@router.get("/isready")
def isready():
    """Polled by the redirect page on the device (a different origin), so it needs CORS."""
    return JSONResponse({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})


@router.get("/", response_class=HTMLResponse)
def shell(request: Request, page: str = ""):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    index = _page_index(caller.config, page)
    response = _render(
        request,
        "shell.html",
        caller,
        {
            "client": _client_config(caller, index),
            **_page_view(caller, index),
            "bg_style": _background_style(caller.config),
        },
    )
    token = request.query_params.get("t")
    if caller.kind == "device" and token:
        # Fragment requests carry the cookie; the kiosk URL keeps ?t= for reloads.
        response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict", max_age=10 * 365 * 86400)
    return response


@router.get("/v/home", response_class=HTMLResponse)
def view_home(request: Request, page: str = ""):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    return _render(request, "home.html", caller, _page_view(caller, _page_index(caller.config, page)))


@router.get("/v/app/{mini}", response_class=HTMLResponse)
def view_list(request: Request, mini: str, kind: str = Query("", alias="list")):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    if mini not in MINI_LABELS:
        return HTMLResponse("", status_code=404)
    if mini == "music":
        if kind in ("speakers", "presets"):
            return _music_list(request, caller, kind)
        return view_music(request)
    if mini == "radio":
        return view_radio(request)
    if mini == "system":
        return _render(request, "system.html", caller, _system_view(state.feed("system")))
    data = state.feed(mini)
    return _render(
        request,
        "list.html",
        caller,
        {"mini": mini, "label": MINI_LABELS[mini], "data": data, "items": data.get("items") or []},
    )


@router.get("/v/item/{mini}", response_class=HTMLResponse)
def view_item(request: Request, mini: str, id: str = ""):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    item = state.feed_item(mini, id) if mini in MINI_LABELS else None
    if item is None:
        return _render(request, "detail.html", caller, {"mini": mini, "label": MINI_LABELS.get(mini, ""), "item": None})
    can_restart = mini == "system" and bool(item.get("unit")) and "restart" in caller.config["allowed_actions"]
    if mini == "system":
        details = state.unit_details(item["unit"]) if item.get("unit") else {"ok": False}
        allowed = set(caller.config["allowed_actions"])
        opens = item["id"] if item["id"] in MINI_LABELS and item["id"] not in ("system", "music", "radio") and f"open:{item['id']}" in allowed else ""
        return _render(request, "service.html", caller, {
            "item": item, "svc": _service_view(item, details), "can_restart": can_restart, "opens": opens,
        })
    paragraphs = [p.strip() for p in str(item.get("detail") or "").split("\n") if p.strip()]
    return _render(
        request,
        "detail.html",
        caller,
        {
            "mini": mini,
            "label": MINI_LABELS[mini],
            "item": item,
            "paragraphs": paragraphs[:12],
            "can_restart": can_restart,
        },
    )


# ── System: the health strip and the services as cards; a service's own page ────────

# Each service's group and icon come from the app catalog, so a new app (Ripple, Radio, the
# next one) gets its tile without a list here: Home apps are "Apps" with icons/apps/<icon>.svg,
# the platform's services (and anything not in the catalog, e.g. the Car Thing) "System" with
# icons/system/<id>.svg. A service without an icon file gets a plain tile.
_BRAND_ICONS = next(
    (p for p in (ROOT_DIR.parents[1] / "packages" / "stonepi_brand" / "static" / "icons",
                 Path("/opt/stonepi/packages/stonepi_brand/static/icons")) if p.is_dir()),
    None,
)
SERVICE_GROUPS = (("system", "System"), ("apps", "Apps"))


def _service_meta(service_id: str) -> tuple[str, str]:
    """(group, icon path under static/brand/icons) for a System feed item."""
    from stonepi_auth.catalog import app_by_id

    item = app_by_id(service_id) or {}
    group = "apps" if item.get("group") == "user" else "system"
    folders = ("apps", "system") if group == "apps" else ("system", "apps")
    for name in dict.fromkeys(n for n in (item.get("icon"), service_id) if n):
        for folder in folders:
            if _BRAND_ICONS is not None and (_BRAND_ICONS / folder / f"{name}.svg").is_file():
                return group, f"{folder}/{name}"
    return group, ""


def _system_service_rank(it: dict) -> tuple:
    """Same order as Dashboard Health: stopped first, then catalog / Home launcher order."""
    from stonepi_auth.catalog import APP_CATALOG, LAUNCHER_APP_IDS

    down = 0 if it.get("level") == "down" else 1
    sid = str(it.get("id") or "")
    if it.get("group") == "apps":
        try:
            return (down, 1, LAUNCHER_APP_IDS.index(sid), sid)
        except ValueError:
            return (down, 1, len(LAUNCHER_APP_IDS), sid)
    system_ids = [item["id"] for item in APP_CATALOG if item.get("group") == "system"]
    try:
        return (down, 0, system_ids.index(sid), sid)
    except ValueError:
        return (down, 0, len(system_ids), sid)


def _system_items(raw_items: list | None) -> list[dict]:
    """Decorate System feed rows with group/icon and sort like Dashboard Health."""
    items = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        group, icon = _service_meta(str(it.get("id") or ""))
        items.append(dict(it, icon=icon, group=group))
    items.sort(key=_system_service_rank)
    return items


# Meter colours: amber from the first number, red from the second.
SYSTEM_LIMITS = {"cpu": (70, 90), "mem": (75, 90), "temp": (65, 78), "disk": (80, 92)}


def _level(key: str, value: int | None) -> str:
    if value is None:
        return ""
    warn, bad = SYSTEM_LIMITS[key]
    return "bad" if value >= bad else "warn" if value >= warn else "ok"


def _system_view(data: dict) -> dict[str, Any]:
    stats = data.get("stats") or {}
    items = _system_items(data.get("items") or [])
    groups = [
        {"id": gid, "label": label, "items": [it for it in items if it["group"] == gid]}
        for gid, label in SERVICE_GROUPS
    ]
    down = [it["title"] for it in items if it.get("level") == "down"]
    meters = [
        {"key": key, "label": label, "value": stats.get(key), "unit": unit, "level": _level(key, stats.get(key))}
        for key, label, unit in (("cpu", "CPU", "%"), ("mem", "Memory", "%"), ("temp", "Temp", "°C"), ("disk", "Disk", "%"))
    ]
    check = list(data.get("alerts") or [])
    if not check:
        check = [f"{name} isn't running" for name in down]
        check += [f"{m['label']} {m['value']}{m['unit']}" for m in meters if m["level"] == "bad"]
        if stats.get("backup") and not stats.get("backup_ok"):
            check.append(f"Backup {stats['backup']}")
    return {"data": data, "stats": stats, "meters": meters, "items": items, "groups": [g for g in groups if g["items"]],
            "check": check[:4], "down": len(down)}


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes and hours < 10 else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def _service_view(item: dict, details: dict) -> dict[str, Any]:
    """The service page's facts: Notify's line and note, plus what Dashboard read from systemd."""
    running = item.get("level") != "down"
    out: dict[str, Any] = {"running": running, "line": item.get("line") or item.get("sub") or "", "note": item.get("note") or "",
                           "icon": _service_meta(str(item.get("id") or ""))[1], "since": "", "memory": "", "restarts": None, "logs": [],
                           "details": bool(details.get("ok")) and not details.get("local")}
    if out["details"]:
        if details.get("state") in ("active", "inactive", "failed"):
            running = details["state"] == "active"
            out["running"] = running
        if details.get("since"):
            out["since"] = _ago(time.time() - int(details["since"]))
        mem = details.get("memory_bytes")
        if isinstance(mem, int) and mem > 0:
            out["memory"] = f"{mem / 1048576:.0f} MB" if mem < 1073741824 else f"{mem / 1073741824:.1f} GB"
        out["restarts"] = details.get("restarts")
        out["logs"] = [line for line in details.get("logs") or [] if isinstance(line, dict)][-4:]
        out["failed"] = details.get("state") == "failed" or details.get("result") not in ("", "success")
    return out


@router.get("/unit-state")
def unit_state(request: Request, unit: str = ""):
    """Polled by the restart screen until the service is running again (only System's units)."""
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False}, status_code=403)
    if not any(it.get("unit") == unit for it in state.feed("system").get("items") or []):
        return JSONResponse({"ok": False}, status_code=404)
    if caller.preview:
        return {"ok": True, "state": "active", "sub": "running"}
    details = state.unit_details(unit)
    return {"ok": bool(details.get("ok")), "state": details.get("state") or "", "sub": details.get("sub") or ""}


@router.get("/v/weather", response_class=HTMLResponse)
def view_weather(request: Request):
    """The large weather screen (U14): now, the next 12 hours and the week."""
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    return _render(request, "weather.html", caller, {"weather": state.weather(caller.config)})


@router.get("/v/audio", response_class=HTMLResponse)
def view_audio(request: Request):
    """Audio launcher (Buttons → Open Audio): Radio then Spotify cards; Back returns."""
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    cfg = caller.config
    return _render(
        request,
        "audio.html",
        caller,
        {
            "header": carthing.page_header(cfg, {}),
            "dots": _page_dots(cfg),
            "weather": state.weather(cfg),
        },
    )


def _saver_clock(config: dict) -> dict:
    """Face and colours the screensaver's clock uses: its own when separate (U13), else the widgets'.

    The Clock style draws it, and so does Show time on the animated styles, at every size.
    """
    clock = config["clock"]
    own = clock.get("saver") or {}
    if own.get("separate"):
        return {"face": own["face"], "color": own["color"], "accent": own["accent"], "separate": True}
    return {"face": clock["face"], "color": clock["color"], "accent": clock["accent"], "separate": False}


def _radio_saver_np(config: dict[str, Any]) -> dict[str, Any] | None:
    """Radio in the artwork screensaver's shape (the Music macros): the station as the track,
    the song (if the station sends it) as the artist, its logo as the artwork, no progress."""
    now = radio.saver_now(_radio_cfg(config)["output"])
    if not now:
        return None
    return {
        "radio": True,
        "track": now["title"],
        "artist": now["song"] or "Radio",
        "album": "",
        "art": f"radio/art?u={quote(now['logo'], safe='')}" if now["logo"] else "",
        "playing": True,
        "connected": True,
        "progress_ms": 0,
        "duration_ms": 0,
        "at_ms": 0,
        "device": {"name": now["output_name"]} if now["output_name"] else None,
    }


@router.get("/radio/art")
def radio_art(request: Request, u: str = ""):
    """A station logo for the panel, fetched from Radio (signed); the panel can't sign in there."""
    caller = _caller(request)
    if not caller.ok:
        return Response(status_code=403)
    found = radio.logo(u) if u else None
    if not found:
        return Response(status_code=404)
    body, media_type = found
    return Response(body, media_type=media_type, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.get("/v/saver", response_class=HTMLResponse)
def view_saver(request: Request):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    show = caller.config["idle"]["show"]
    if _music_cfg(caller.config)["saver_artwork"]:
        # Artwork while something plays: Spotify first, then Radio on the panel's Play on output.
        np = _np(NP_AGE["saver"]) if music.player.connected() else None
        if not (np and np["playing"] and np["track"]):
            np = _radio_saver_np(caller.config)
        if np:
            skin = _saver_skin(caller.config, np)
            if skin:
                return _render(request, "saver.html", caller, {"skin": skin, "weather": None, "live": None, "saver_clock": _saver_clock(caller.config)})
            look = _music_options(caller.config)
            return _render(request, "saver.html", caller, {"music": np, "music_look": {"look": look["look"], "accent": look["accent"]}, "weather": None, "live": None, "saver_clock": _saver_clock(caller.config)})
    if caller.config["idle"]["saver"]["style"] == "web":
        opts = caller.config["idle"]["saver"]["web"]
        names = state.web_page_names(caller.panel)
        web = {
            "page_id": opts["page_id"],
            "missing": opts["page_id"] not in names,
            # How often the panel asks; the panel keeps the snapshot for the page's refresh time.
            "every_s": WEB_WIDGET_EVERY_S,
        }
        return _render(request, "saver.html", caller, {"web": web, "weather": None, "live": None, "saver_clock": _saver_clock(caller.config)})
    if caller.config["idle"]["saver"]["style"] != "clock":
        # Date, weather and the live line are clock-only; the animations need no data.
        return _render(request, "saver.html", caller, {"weather": None, "live": None, "saver_clock": _saver_clock(caller.config)})
    live = None
    if show.get("live_line") == "radio":
        now = radio.now_playing(NP_AGE["saver"], _radio_cfg(caller.config)["output"])
        if now.get("connected") and (now.get("title") or now.get("playing")):
            live = {"label": "Radio", "text": str(now.get("title") or "On air")[:80]}
    elif show.get("live_line") == "music":
        np = _np(NP_AGE["saver"]) if music.player.connected() else None
        if np and np["track"] and np["active"]:
            text = np["track"] + (f" — {np['artist']}" if np["artist"] else "")
            live = {"label": "♪", "text": text[:80]}
    elif show.get("live_line") and show["live_line"] != "none":
        card = state.feed(show["live_line"]).get("card") or {}
        if card.get("headline"):
            live = {"label": MINI_LABELS.get(show["live_line"], ""), "text": str(card["headline"])[:80]}
    return _render(
        request,
        "saver.html",
        caller,
        {
            "weather": state.weather(caller.config) if show.get("weather") else None,
            "live": live,
            "saver_clock": _saver_clock(caller.config),
        },
    )


def _restart_target(caller: Caller, unit: str) -> dict | None:
    if "restart" not in caller.config["allowed_actions"]:
        return None
    for item in state.feed("system").get("items") or []:
        if item.get("unit") and item["unit"] == unit:
            return item
    return None


@router.get("/v/restart", response_class=HTMLResponse)
def view_restart(request: Request, unit: str = ""):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    target = _restart_target(caller, unit)
    if target is None:
        return HTMLResponse("", status_code=404)
    pin = caller.panel.get("pin") or {}
    return _render(
        request,
        "restart.html",
        caller,
        {
            "target": target,
            "icon": _service_meta(str(target.get("id") or ""))[1],
            "needs_pin": bool(pin.get("enabled")),
            "pin_length": carthing.PIN_LENGTH,
            "locked_for": state.pin_guard.locked_for(),
        },
    )


@router.get("/v/restarting", response_class=HTMLResponse)
def view_restarting(request: Request, unit: str = ""):
    """After a restart: stopped, starting, running, as panel.js polls /unit-state."""
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    target = _restart_target(caller, unit)
    if target is None:
        return HTMLResponse("", status_code=404)
    return _render(request, "restarting.html", caller, {"target": target, "icon": _service_meta(str(target.get("id") or ""))[1]})


@router.post("/restart")
async def do_restart(request: Request):
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False, "message": "Not paired."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    target = _restart_target(caller, str(body.get("unit") or ""))
    if target is None:
        return JSONResponse({"ok": False, "message": "That service can't be restarted here."}, status_code=400)
    pin = caller.panel.get("pin") or {}
    if pin.get("enabled") and caller.preview:
        # Admin trying the editor preview: check the PIN, but never lock the real device out.
        if not carthing.pin_ok(str(body.get("pin") or ""), pin):
            return JSONResponse({"ok": False, "message": "Wrong PIN.", "wrong_pin": True, "locked": 0})
    elif pin.get("enabled"):
        locked = state.pin_guard.locked_for()
        if locked:
            return JSONResponse({"ok": False, "message": f"Too many wrong PINs. Try again in {locked}s.", "locked": locked})
        good = carthing.pin_ok(str(body.get("pin") or ""), pin)
        state.pin_guard.record(
            good, max_attempts=int(pin.get("max_attempts") or 5), lockout_s=int(pin.get("lockout_s") or 300)
        )
        if not good:
            locked = state.pin_guard.locked_for()
            message = f"Wrong PIN. Locked for {locked}s." if locked else "Wrong PIN."
            return JSONResponse({"ok": False, "message": message, "wrong_pin": True, "locked": locked})
    if caller.preview:
        return {"ok": True, "message": f"Preview: {target.get('title')} would restart now."}
    result = state.restart_unit(target["unit"])
    if result["ok"]:
        state.clear_cache()
        result["message"] = result["message"] or f"Restarted {target.get('title')}."
    return result


@router.get("/tick")
def tick(request: Request):
    """The device checks in every 15 s: config rev, feed rev, and the Pi's clock."""
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False, "paired": False}, status_code=403)
    cfg = caller.config
    shown = list(dict.fromkeys(carthing.page_widget_ids(cfg) + [cfg["idle"]["show"].get("live_line") or "none"]))
    shown = [m for m in shown if m in MINI_LABELS]
    from app.connector import connector

    if caller.kind == "device":
        connector.note_tick()
    frev = state.feeds_rev([m for m in shown if m not in ("music", "radio")])
    # Music on a page, as the live line, or the artwork screensaver: follow the track too.
    music_age = NP_AGE["widget"] if "music" in shown else NP_AGE["saver"] if _music_cfg(cfg)["saver_artwork"] else 0
    if music_age and music.player.connected():
        frev = hashlib.sha256(f"{frev}|{music.player.signature(music_age)}".encode("utf-8")).hexdigest()[:12]
    # Radio on a page, or the artwork screensaver (which shows Radio when Spotify isn't playing).
    # For the screensaver only play/stop matters (artwork ↔ style); song/title changes are
    # painted in place by the panel's radio poll so a tick doesn't flash the whole screen.
    if "radio" in shown or _music_cfg(cfg)["saver_artwork"]:
        now = radio.now_playing(NP_AGE["widget"] if "radio" in shown else NP_AGE["saver"], _radio_cfg(cfg)["output"])
        if "radio" in shown:
            frev = hashlib.sha256(
                f"{frev}|radio|{now.get('title')}|{now.get('playing')}|{now.get('volume')}".encode("utf-8")
            ).hexdigest()[:12]
        elif now.get("playing"):
            # Artwork screensaver only: play/stop flips the rev; title paints in place.
            frev = hashlib.sha256(f"{frev}|radio|1".encode("utf-8")).hexdigest()[:12]
    return {
        "ok": True,
        "rev": caller.panel.get("rev") or "",
        "frev": frev,
        "backlight": caller.kind == "device" and connector.has_backlight(),
        **_clock_offset(),
    }


@router.post("/screen")
async def screen(request: Request):
    """Backlight level from panel.js (idle dimming, quiet hours, brightness buttons)."""
    from app.connector import connector

    caller = _caller(request)
    if caller.kind != "device":
        return {"ok": False}
    try:
        body = await request.json()
        level = int(body.get("level"))
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    import asyncio

    ok = await asyncio.to_thread(connector.set_level, level)
    return {"ok": ok}


@router.post("/clientlog")
async def clientlog(request: Request):
    """Script errors from panel.js (the device has no devtools) -> the service journal."""
    caller = _caller(request)
    if not caller.ok:
        return Response(status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    msg = str(body.get("msg") or "")[:400].replace("\n", " ")
    ua = str(body.get("ua") or "")[:160]
    logger.warning("panel (%s) %s [%s]", caller.kind, msg, ua)
    return Response(status_code=204)


def _web_target(caller: Caller, name: str, s: str, ext: str) -> tuple[str, str] | None:
    """(page id, where) for a snapshot this panel shows, or None."""
    match = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,39})\." + ext, name)
    if not match:
        return None
    page_id = match.group(1)
    cfg = caller.config
    where = s if s in ("saver", "page") else "widget"
    if where == "saver":
        opts = cfg["idle"]["saver"]["web"]
        if cfg["idle"]["saver"]["style"] != "web" or opts["page_id"] != page_id:
            return None
    elif f"{carthing.WEB_WIDGET_PREFIX}{page_id}" not in carthing.page_widget_ids(cfg):
        return None
    return page_id, where


@router.get("/web/{name}")
def web_image(request: Request, name: str, s: str = "widget", fresh: int = 0):
    """A web page snapshot (F14), from Notify's capture. The panel asks only while it shows; a
    snapshot is kept for the page's refresh time (Notify → Web pages), ``fresh`` (the ↻ button)
    takes a new one."""
    caller = _caller(request)
    if not caller.ok:
        return Response(status_code=403)
    if name.endswith(".json"):
        return _web_stamp(caller, name, s)
    target = _web_target(caller, name, s, "png")
    if target is None:
        return Response(status_code=404)
    page_id, where = target
    max_age = state.web_page_refresh_s(caller.panel, page_id) - 5
    width, height = WEB_SIZES[where]
    rev = state.web_page_rev(caller.panel, page_id)
    data = state.web_snapshot(page_id, width, height, max_age, fresh=bool(fresh), rev=rev)
    if data is None:
        return Response(status_code=502)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


def _web_stamp(caller: Caller, name: str, s: str) -> Response:
    """The "Updated 14:05" line under a snapshot: when Notify captured the one the panel has."""
    target = _web_target(caller, name, s, "json")
    if target is None:
        return Response(status_code=404)
    page_id, where = target
    at = state.web_snapshot_at(page_id, *WEB_SIZES[where], rev=state.web_page_rev(caller.panel, page_id))
    text = ""
    if at:
        when = datetime.fromtimestamp(at)
        text = "Updated " + when.strftime("%H:%M" if when.date() == datetime.now().date() else "%a %H:%M")
    return JSONResponse({"at": int(at or 0), "text": text}, headers={"Cache-Control": "no-store"})


@router.get("/bg/{asset_id}")
def background(request: Request, asset_id: str):
    caller = _caller(request)
    if not caller.ok:
        return Response(status_code=403)
    path = state.background_path(asset_id)
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000, immutable"})


# ── Music (F7): Now Playing, speakers, presets, commands, artwork ────────────

# How old the cached Spotify state may be, by what's asking (carthing-spotify.md §5).
NP_AGE = {"screen": 3.0, "widget": 10.0, "saver": 30.0}
# Panel commands → the action that must be allowed for them.
MUSIC_CMD_ACTION = {
    "play_pause": "music:play_pause",
    "next": "music:next",
    "prev": "music:prev",
    "shuffle": "music:shuffle",
    "repeat": "music:repeat",
    "save": "music:save",
    "transfer": "open:music",
    # Dragging or tapping the progress bar: allowed wherever play/pause is.
    "seek": "music:play_pause",
}


def _np(max_age: float) -> dict[str, Any]:
    return music.player.now_playing(max_age)


def _music_cfg(config: dict) -> dict[str, Any]:
    return config.get("music") or carthing.default_music()


def _music_options(config: dict, np: dict | None = None) -> dict[str, Any]:
    """What Now Playing may show and do. With ``np`` (connected), the preset tiles get their artwork."""
    opts = _music_cfg(config)
    allowed = set(config["allowed_actions"])
    # The tiles are drawn with Now Playing even while music plays (they show when it stops);
    # each preset's artwork is looked up once.
    want_art = np is not None and np.get("connected")
    presets = []
    for n, p in enumerate(opts.get("presets") or [], start=1):
        row = {"number": n, "name": p.get("name") or "", "set": bool(p.get("uri")), "allowed": f"music:preset:{n}" in allowed, "art": ""}
        if want_art and row["set"] and row["allowed"]:
            row["art"] = music.player.preset_art(p["uri"])
        presets.append(row)
    look = opts.get("np") or dict(carthing.NP_DEFAULTS)
    return {
        "show_save": bool(opts.get("show_save")) and "music:save" in allowed,
        "presets": presets,
        "allowed": {cmd: action in allowed for cmd, action in MUSIC_CMD_ACTION.items()},
        "look": look,
        # The panel's own accent, for "The panel's accent" and covers with no clear colour.
        "accent": (config.get("look") or {}).get("accent") or carthing.SAND,
        "default_device": (opts.get("default_device") or {}).get("name") or "",
    }


@router.get("/v/music", response_class=HTMLResponse)
def view_music(request: Request):
    """Now Playing: artwork, track, progress, speaker and volume; controls in the focus list."""
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    np = _np(NP_AGE["screen"])
    cfg = caller.config
    return _render(request, "music.html", caller, {
        "np": np,
        "skin": _music_skin(cfg, np) if _music_cfg(cfg).get("look_from") == "radio" and np.get("connected") else None,
        "opts": _music_options(cfg, np),
        # The header follows the config's default, like the pages (no page is lit in the dots).
        "header": carthing.page_header(cfg, {}),
        "dots": _page_dots(cfg),
        "weather": state.weather(cfg),
    })


def _radio_cfg(config: dict) -> dict[str, Any]:
    # Defaults underneath, so a config saved before "output" existed plays on StonePi as before.
    return {**carthing.default_radio(), **(config.get("radio") or {})}


def _radio_options(config: dict, now: dict | None = None) -> dict[str, Any]:
    opts = _radio_cfg(config)
    allowed = set(config["allowed_actions"])
    presets = []
    favs = list((now or {}).get("presets") or [])
    for n, p in enumerate(opts.get("presets") or [], start=1):
        fav = favs[n - 1] if n - 1 < len(favs) else {}
        set_flag = bool(p.get("station_id") or p.get("url")) or bool(fav)
        name = p.get("name") or fav.get("name") or f"Preset {n}"
        logo = str(fav.get("favicon") or "")
        presets.append(
            {
                "number": n,
                "name": name,
                "set": set_flag,
                "allowed": f"radio:preset:{n}" in allowed,
                "art": f"radio/art?u={quote(logo, safe='')}" if logo else "",
            }
        )
    return {
        "theme": opts.get("theme") or "winamp",
        "presets": presets,
        "allowed": {
            "play_pause": "radio:play_pause" in allowed,
            "stop": "radio:stop" in allowed,
            "vol_up": "radio:vol_up" in allowed,
            "vol_down": "radio:vol_down" in allowed,
        },
    }


@router.get("/v/radio", response_class=HTMLResponse)
def view_radio(request: Request):
    caller = _caller(request)
    if not caller.ok:
        return _denied(request, caller)
    cfg = caller.config
    now = radio.now_playing(NP_AGE["screen"], _radio_cfg(cfg)["output"])
    return _render(
        request,
        "radio.html",
        caller,
        {
            "now": now,
            "opts": _radio_options(cfg, now),
            # Look → Radio → Radio plays in Now Playing: Music's Now Playing, driving Radio.
            "np_look": (
                {"np": _radio_np(cfg, now), "opts": _radio_np_options(cfg, now)}
                if _radio_cfg(cfg).get("look_from") == "music"
                else None
            ),
            "header": carthing.page_header(cfg, {}),
            "dots": _page_dots(cfg),
            "weather": state.weather(cfg),
        },
    )


# ── Each player in the other's look (Look → Music / Look → Radio → "plays in") ──


def _radio_np(config: dict, now: dict[str, Any]) -> dict[str, Any]:
    """Radio in Now Playing's shape (the Music macros): the station as the track, the song (when
    the station sends it) under it, its logo as the artwork, the volume on the speaker row."""
    extra = radio.saver_now(_radio_cfg(config)["output"]) if now.get("playing") else None
    logo = (extra or {}).get("logo") or ""
    volume = now.get("volume")
    return {
        "radio": True,
        "connected": bool(now.get("connected")),
        "track": now.get("title") or "",
        "artist": (extra or {}).get("song") or now.get("artist") or ("On air" if now.get("playing") else ""),
        "album": "",
        "art": f"radio/art?u={quote(logo, safe='')}" if logo else "",
        "playing": bool(now.get("playing")),
        "progress_ms": 0,
        "duration_ms": 0,
        "at_ms": 0,
        "error": now.get("error") or "",
        "device": {
            "name": now.get("output_name") or "",
            "supports_volume": volume is not None and bool(now.get("connected")),
            "volume": round(float(volume) * 100) if volume is not None and now.get("connected") else None,
        },
    }


def _radio_np_options(config: dict, now: dict[str, Any]) -> dict[str, Any]:
    """Now Playing's look (Look → Music) with Radio's buttons and presets."""
    look = _music_options(config)
    ropts = _radio_options(config, now)
    return {
        "look": {**look["look"], "controls": "always"},
        "accent": look["accent"],
        "presets": [{**p, "art": ""} for p in ropts["presets"]],
        "allowed": ropts["allowed"],
        "show_save": False,
        "default_device": "",
    }


def _music_skin(config: dict, np: dict[str, Any]) -> dict[str, Any]:
    """Spotify in the Radio player's shape (the Radio skin macros): the track as the station,
    the artist under it; the skin's buttons drive Spotify (panel.js maps them)."""
    d = np.get("device") or {}
    playing = bool(np.get("playing"))
    now = {
        "connected": bool(np.get("connected")),
        "playing": playing,
        "paused": bool(np.get("track")) and not playing,
        "title": np.get("track") or "",
        "artist": np.get("artist") or "",
        "volume": (d.get("volume") / 100) if d.get("volume") is not None else None,
        "source": "spotify",
        "playback_status": "Playing" if playing else "Paused" if np.get("track") else "Stopped",
        "error": np.get("error") or "",
        "presets": [],
        # Winamp's own track readouts: elapsed time, the position bar, shuffle and repeat.
        "progress_ms": int(np.get("progress_ms") or 0),
        "duration_ms": int(np.get("duration_ms") or 0),
        "at_ms": int(np.get("at_ms") or 0),
        "shuffle": bool(np.get("shuffle")),
        "repeat": str(np.get("repeat") or "off"),
        # Spotify doesn't say its bitrate (it depends on the account and the speaker), but
        # everything it plays is 44.1 kHz, so the kHz box is filled and kbps left blank.
        "kbps": 0,
        "khz": 44 if playing else 0,
    }
    mopts = _music_options(config)
    actions = set(config["allowed_actions"])
    opts = {
        "theme": _radio_cfg(config).get("theme") or "winamp",
        # The skin is showing Spotify: Previous / Next skip tracks, Shuffle and Repeat work.
        "music": True,
        "presets": [{k: p[k] for k in ("number", "name", "set", "allowed")} for p in mopts["presets"]],
        "allowed": {
            "prev": mopts["allowed"]["prev"],
            "next": mopts["allowed"]["next"],
            "shuffle": mopts["allowed"]["shuffle"],
            "repeat": mopts["allowed"]["repeat"],
            "play_pause": mopts["allowed"]["play_pause"],
            # Spotify has no stop: the skin's stop pauses.
            "stop": mopts["allowed"]["play_pause"],
            "vol_up": "music:vol_up" in actions,
            "vol_down": "music:vol_down" in actions,
        },
    }
    key = f"{now['title']}|{now['artist']}|{int(playing)}|{int(now['shuffle'])}|{now['repeat']}"
    return {"now": now, "opts": opts, "key": key}


def _saver_skin(config: dict, np: dict[str, Any]) -> dict[str, Any] | None:
    """The artwork screensaver in a Radio skin, when the source playing wears one: Radio by
    default (Look → Radio → Radio plays in), Spotify when Look → Music says Radio's theme."""
    if np.get("radio"):
        if _radio_cfg(config).get("look_from") != "radio":
            return None
        now = radio.now_playing(NP_AGE["saver"], _radio_cfg(config)["output"])
        return {"src": "radio", "now": now, "opts": _radio_options(config, now), "key": ""}
    if _music_cfg(config).get("look_from") != "radio":
        return None
    return {"src": "music", **_music_skin(config, np)}


@router.get("/radio/now")
def radio_now(request: Request, src: str = "screen"):
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True, **radio.now_playing(NP_AGE.get(src, NP_AGE["screen"]), _radio_cfg(caller.config)["output"])}


@router.post("/radio/cmd")
async def radio_cmd(request: Request):
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False, "message": "Not paired."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    cmd, arg = str(body.get("cmd") or ""), body.get("arg")
    cfg = caller.config
    allowed = set(cfg["allowed_actions"])
    opts = _radio_cfg(cfg)
    if cmd == "vol":
        try:
            delta = max(-50, min(50, int(arg)))
        except (TypeError, ValueError):
            delta = 0
        action = "radio:vol_up" if delta > 0 else "radio:vol_down"
        delta = max(-10, min(10, delta))  # steps: one per button press, a batch from the dial
    elif cmd == "preset":
        try:
            number = int(arg)
        except (TypeError, ValueError):
            number = 0
        action = f"radio:preset:{number}"
    elif cmd == "play_pause":
        action = "radio:play_pause"
    elif cmd == "stop":
        action = "radio:stop"
    else:
        action = ""
    if not action or action not in allowed:
        return JSONResponse({"ok": False, "message": "That isn't allowed on this panel."}, status_code=403)
    import asyncio

    def run() -> tuple[str, float | None]:
        if cmd == "play_pause":
            radio.transport("play_pause", output=opts["output"])
            return "", None
        if cmd == "stop":
            radio.transport("stop", output=opts["output"])
            return "", None
        if cmd == "vol":
            vol: float | None = None
            if delta:
                step = max(1, int(opts.get("volume_step") or 5)) / 100.0
                radio.volume(delta=step * delta, output=opts["output"])
                now = radio.now_playing(0, opts["output"])
                vol = float(now.get("volume") or 0)
                return f"Volume {int(round(vol * 100))}%", vol
            return "", None
        if cmd == "preset":
            state = radio.play_preset(cfg, number)
            name = ((state.get("station") or {}).get("name") if isinstance(state.get("station"), dict) else None) or state.get("title") or f"preset {number}"
            return f"Playing {name}.", None
        return "", None

    try:
        message, volume = await asyncio.to_thread(run)
    except Exception:
        logger.exception("radio command failed")
        return {"ok": False, "message": "That didn't work."}
    out: dict[str, Any] = {"ok": True, "message": message}
    if volume is not None:
        out["volume"] = volume  # 0–1; the Winamp skin updates its slider without a full reload
    return out


def _music_list(request: Request, caller: Caller, kind: str):
    opts = _music_options(caller.config)
    rows: list[dict[str, Any]] = []
    message = ""
    if kind == "speakers":
        if not opts["allowed"]["transfer"]:
            message = "Choosing a speaker isn't allowed in this config."
        else:
            try:
                rows = music.player.devices(max_age=3.0)
            except music.MusicError as exc:
                message = str(exc)
            if not rows and not message:
                message = "No speakers right now. Open Spotify on one to wake it."
    else:
        rows = [p for p in opts["presets"] if p["set"]]
        if not rows:
            message = "No music presets yet. Choose them in Notify → Displays → Car Thing → Music."
    return _render(request, "music_list.html", caller, {"kind": kind, "rows": rows, "message": message, "np": _np(NP_AGE["widget"])})


@router.get("/np")
def now_playing(request: Request, src: str = "screen"):
    """Small JSON the device polls while Now Playing (or a music widget) is on screen."""
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True, **_np(NP_AGE.get(src, NP_AGE["screen"]))}


@router.post("/music/cmd")
async def music_cmd(request: Request):
    """One Music command from the panel. Every one goes through the config's allowed actions."""
    caller = _caller(request)
    if not caller.ok:
        return JSONResponse({"ok": False, "message": "Not paired."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    cmd, arg = str(body.get("cmd") or ""), body.get("arg")
    cfg = caller.config
    allowed = set(cfg["allowed_actions"])
    opts = _music_cfg(cfg)
    if cmd == "vol":
        try:
            delta = max(-50, min(50, int(arg)))
        except (TypeError, ValueError):
            delta = 0
        action = "music:vol_up" if delta > 0 else "music:vol_down"
    elif cmd == "preset":
        try:
            number = int(arg)
        except (TypeError, ValueError):
            number = 0
        action = f"music:preset:{number}"
    else:
        action = MUSIC_CMD_ACTION.get(cmd, "")
    if not action or action not in allowed:
        return JSONResponse({"ok": False, "message": "That isn't allowed on this panel."}, status_code=403)
    import asyncio

    def run() -> str:
        player = music.player
        if cmd == "play_pause":
            player.play_pause(opts)
        elif cmd in ("next", "prev"):
            player.skip(cmd == "next")
        elif cmd == "vol":
            if delta:
                return f"Volume {player.volume(delta)}%"
        elif cmd == "shuffle":
            player.shuffle()
        elif cmd == "repeat":
            player.repeat()
        elif cmd == "save":
            track, liked = player.save()
            return f"Saved {track} to Liked Songs." if liked else f"Removed {track} from Liked Songs."
        elif cmd == "seek":
            try:
                player.seek(int(arg))
            except (TypeError, ValueError) as exc:
                raise music.MusicError("bad", "That isn't a position in the song.") from exc
        elif cmd == "transfer":
            player.transfer(str(arg or ""))
        elif cmd == "preset":
            name = player.preset(number, opts)
            return f"Playing {name}." if name else ""
        return ""

    try:
        message = await asyncio.to_thread(run)
    except music.MusicError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    return {"ok": True, "message": message}


@router.get("/art/{key}")
def artwork(request: Request, key: str):
    caller = _caller(request)
    if not caller.ok:
        return Response(status_code=403)
    path = music.player.art_path(key)
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800, immutable"})


@router.get("/internal/music/catalog")
def internal_music_catalog(request: Request):
    """Notify's Music tab: speakers and playlists to choose presets from."""
    from stonepi_auth.internal import verify_internal

    if not verify_internal(state.session_secret(), "GET", "/internal/music/catalog", request.headers):
        return JSONResponse({"ok": False}, status_code=403)
    return music.catalog()


@router.get("/internal/status")
def internal_status(request: Request):
    """Notify's Device tab: is the Car Thing checking in?"""
    from stonepi_auth.internal import verify_internal

    from app.connector import connector

    if not verify_internal(state.session_secret(), "GET", "/internal/status", request.headers):
        return JSONResponse({"ok": False}, status_code=403)
    return {
        "ok": True,
        "connected": state.device_seen.connected(),
        "last_seen": state.device_seen.wall,
        **connector.status(),
        "music": music.status(),
    }


@router.post("/internal/repair")
def internal_repair(request: Request):
    """Notify's Re-pair button: push a page with a fresh token on the next connector pass."""
    from stonepi_auth.internal import verify_internal

    from app.connector import connector

    if not verify_internal(state.session_secret(), "POST", "/internal/repair", request.headers):
        return JSONResponse({"ok": False}, status_code=403)
    state.clear_cache()
    connector.repair()
    return {"ok": True}


@router.post("/internal/music/reset")
def internal_music_reset(request: Request):
    """Notify changed the Spotify sign-in: drop the access token and reread the vault."""
    from stonepi_auth.internal import verify_internal

    if not verify_internal(state.session_secret(), "POST", "/internal/music/reset", request.headers):
        return JSONResponse({"ok": False}, status_code=403)
    music.reset()
    return {"ok": True}


@router.post("/internal/music/check")
def internal_music_check(request: Request):
    """Notify's Test connection button."""
    from stonepi_auth.internal import verify_internal

    if not verify_internal(state.session_secret(), "POST", "/internal/music/check", request.headers):
        return JSONResponse({"ok": False}, status_code=403)
    return music.check()


def client_json(data: dict) -> str:
    """JSON safe inside <script type=application/json>."""
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


templates.env.filters["client_json"] = client_json
