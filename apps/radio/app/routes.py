from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import ROOT_DIR, env
from app.services import art, discover, dlna, outputs, stations, streams
from app.services.backend import client as odio_client

from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.internal import verify_internal_v2
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, decode_session, factory_admin_warning

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(
    asset_rev=__asset_rev__, fonts_rev=fonts_rev(), art_url=art.art_url, station_label=stations.station_label
)
add_shared_templates(templates.env)
router = APIRouter()

# Household settings (General: default volume and Discover's country; Odio: the player's
# status) are for admins only and hidden from members, who see Notifications and About.
HOUSEHOLD_TABS = frozenset({"speakers", "odio"})

# The player bar on My Radio / Discover / Settings (each person's own): off, only while This
# device plays (the default), or also for a speaker / StonePi.
PLAYER_BAR_KEY = "player_bar"
PLAYER_BAR_MODES = (
    ("device", "When playing on This device", "The bar shows while a station plays in this browser."),
    ("always", "Wherever I play", "Also for a speaker or StonePi: what's on, with play/pause and stop."),
    ("off", "Off", "No bar; Now Playing has the controls."),
)


# Where the bar sits, each person's own. On phones the corners are the bottom, full width.
PLAYER_POS_KEY = "player_bar_pos"
PLAYER_POSITIONS = (
    ("bottom-left", "Bottom left"),
    ("bottom-right", "Bottom right"),
    ("top-right", "Top right"),
)


def _player_bar_pos(user) -> str:
    pos = db.get_person_pref(_owner(user), PLAYER_POS_KEY, "bottom-left")
    return pos if pos in {p[0] for p in PLAYER_POSITIONS} else "bottom-left"


def _player_bar_mode(user) -> str:
    mode = db.get_person_pref(_owner(user), PLAYER_BAR_KEY, "device")
    return mode if mode in {m[0] for m in PLAYER_BAR_MODES} else "device"


# My Radio's two sections, in each person's own order.
MY_RADIO_FIRST_KEY = "my_radio_first"
MY_RADIO_FIRST = (
    ("home", "Home stations", "The household's stations, then your favourites."),
    ("favourites", "My favourites", "Your starred stations, then the household's."),
)


def _my_radio_first(user) -> str:
    first = db.get_person_pref(_owner(user), MY_RADIO_FIRST_KEY, "home")
    return first if first in {m[0] for m in MY_RADIO_FIRST} else "home"


# Admins can hide My Radio's floating "Add station" button for themselves (Discover still adds).
SHOW_ADD_KEY = "show_add_station"


def _show_add(user) -> bool:
    return db.get_person_pref(_owner(user), SHOW_ADD_KEY, "1") != "0"


# Now Playing's four presets, each person's own: their latest favourites, or four they pick
# from their favourites and the Home stations.
PRESET_COUNT = 4
PRESETS_MODE_KEY = "presets_mode"
PRESETS_KEY = "presets"
PRESETS_MODES = (
    ("latest", "Your four latest favourites", "The stations you starred most recently."),
    ("set", "Choose them", "Pick four stations from your favourites and the Home stations."),
)


def _presets_mode(user) -> str:
    mode = db.get_person_pref(_owner(user), PRESETS_MODE_KEY, "latest")
    return mode if mode in {m[0] for m in PRESETS_MODES} else "latest"


def _preset_choices(owner: str) -> list[dict]:
    """What a preset can be: your favourites, then the Home stations (each station once)."""
    seen: set[str] = set()
    out = []
    for station in db.list_favorites(owner) + db.list_stations():
        if station["stream_url"] not in seen:
            seen.add(station["stream_url"])
            out.append(station)
    return out


def _chosen_preset_urls(user) -> list[str]:
    try:
        urls = json.loads(db.get_person_pref(_owner(user), PRESETS_KEY, "[]") or "[]")
    except ValueError:
        urls = []
    urls = [str(u) for u in urls if isinstance(u, str)][:PRESET_COUNT]
    return urls + [""] * (PRESET_COUNT - len(urls))


def _presets(user) -> list[dict]:
    """Now Playing's presets. Chosen ones that have gone (unstarred, removed) are skipped; with
    none left it falls back to the latest favourites."""
    owner = _owner(user)
    if _presets_mode(user) == "set":
        by_url = {s["stream_url"]: s for s in _preset_choices(owner)}
        picked = [by_url[u] for u in _chosen_preset_urls(user) if u in by_url]
        if picked:
            return picked
    return db.list_favorites(owner)[:PRESET_COUNT]


SETTINGS_GROUPS = (
    (
        "prefs",
        "Preferences",
        (
            ("general", "General", "Display options, playback and Discover", "general"),
            ("speakers", "Speakers", "Speakers found on the home network", "speaker"),
            ("odio", "Odio", "Local audio backend connection", "sources"),
        ),
    ),
    (
        "more",
        "More",
        (
            ("notifications", "Notifications", "Phone alerts for this household", "notifications"),
            ("about", "About", "Version and project links", "about"),
        ),
    ),
)


def _session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def _prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="radio",
        prefix=_prefix(),
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _require_user(request: Request):
    user = _user(request)
    secret = _session_secret()
    if secret and user is None:
        return None, RedirectResponse(login_url(_settings(), f"{_prefix()}/"), status_code=303)
    if user and not user.is_admin and not user.can_access("radio"):
        response = _csrf_response(request, "denied.html", {"user": user, "active": ""})
        response.status_code = 403
        return None, response
    return user, None


def _is_admin(user) -> bool:
    """Household settings and Home stations are admin-only; anyone when there's no sign-in."""
    return not _session_secret() or bool(user and user.is_admin)


def _owner(user) -> str:
    """Whose favorites: the signed-in person; "" when Radio runs without sign-in (solo dev)."""
    return user.user_id if user else ""


def _mark_favorites(stations: list[dict], owner: str) -> list[dict]:
    urls = db.favorite_urls(owner)
    for station in stations:
        station["favorite"] = station.get("stream_url") in urls
    return stations


def _safe_next(value: str | None, default: str) -> str:
    """A path inside Radio only: no scheme, host or protocol-relative ``//`` (open redirect)."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("stonepi_home_url", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    ctx.setdefault("app_name", "Radio")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(_session_secret()))
    ctx.setdefault("mock", env.mock)
    user = ctx.get("user")
    ctx.setdefault("using_factory_admin", factory_admin_warning(user))
    ctx.setdefault("player_bar_mode", _player_bar_mode(user))
    ctx.setdefault("player_bar_pos", _player_bar_pos(user))
    ctx.setdefault(
        "alerts_bell_state",
        bell_context(
            user,
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=ctx["stonepi_home_url"],
            enabled=bool(_session_secret()),
            app_id="radio",
        ),
    )
    response = templates.TemplateResponse(request, name, ctx)
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _redirect(path: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    path = _safe_next(path, "/")
    pfx = _prefix()
    url = f"{pfx}{path}" if path.startswith("/") else f"{pfx}/{path}"
    parts = []
    if msg:
        parts.append(f"msg={quote(msg)}")
    if error:
        parts.append(f"error={quote(error)}")
    if parts:
        # The page may have its own query (Discover's ?genre=jazz): add to it, don't start another.
        url = f"{url}{'&' if '?' in url else '?'}{'&'.join(parts)}"
    return RedirectResponse(url, status_code=303)


def _now_safe(output_id: str = outputs.STONEPI) -> dict:
    """What's playing on a server-side output; never raises (pages and displays show it)."""
    if output_id == outputs.BROWSER:
        return {"ok": True, "browser": True, "output": outputs.BROWSER, "output_name": "This device",
                "connected": True, "playing": False, "paused": False, "playback_status": "", "title": "",
                "artist": "", "art_url": "", "volume": None, "source": "browser"}
    try:
        return outputs.now(output_id)
    except Exception as exc:
        return {
            "ok": False,
            "connected": False,
            "output": output_id,
            "output_name": outputs.name_of(output_id),
            "source": "none",
            "playback_status": "Stopped",
            "playing": False,
            "paused": False,
            "title": "",
            "artist": "",
            "art_url": "",
            "volume": float(db.get_pref("default_volume", str(env.default_volume)) or env.default_volume),
            "error": str(exc),
            "player": {},
            "players": [],
        }


def _fetched(request: Request) -> bool:
    """A background post from the page's script (the standard: X-Requested-With: fetch)."""
    return request.headers.get("x-requested-with") == "fetch"


def _reply(request: Request, ok: bool, message: str = "", *, back: str = "/now", status: int = 400, **extra):
    """The standard answer: JSON {ok, message, …} for a background post, else a redirect."""
    if _fetched(request):
        return JSONResponse({"ok": ok, "message": message, **extra}, status_code=200 if ok else status)
    return _redirect(back, msg=message if ok and message else None, error=None if ok else message)


def _https_page(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "radio", "mock": env.mock}


@router.get("/api/display")
def api_display():
    # Loopback feed for Home and the Car Thing (blocked at the public edge). No one is signed
    # in to a display, so its presets are the household's most-saved stations.
    now = _now_safe()
    favs = db.household_favorites(4)
    detail = now.get("title") or ("Stopped" if not now.get("playing") else "On air")
    return JSONResponse(
        {
            "ok": True,
            "playing": bool(now.get("playing")),
            "title": now.get("title") or "",
            "artist": now.get("artist") or "",
            "volume": now.get("volume"),
            "source": now.get("source") or "none",
            "detail": detail,
            "card": {
                "headline": now.get("title") or ("On air" if now.get("playing") else "Idle"),
                "sub": now.get("artist") or detail,
                "badge": "Live" if now.get("playing") else "",
            },
            "presets": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "stream_url": f["stream_url"],
                    "favicon": f.get("favicon") or "",
                }
                for f in favs
            ],
            "items": [
                {
                    "title": f["name"],
                    "sub": f.get("country") or f.get("tags") or "Favorite",
                    "id": f["id"],
                }
                for f in favs
            ],
        }
    )


# Car Thing control: signed service-to-service calls (stonepi_auth.internal); nginx also
# blocks /radio/api/internal/ at the edge. Without a session secret only dev may call them.
def _internal_ok(request: Request, body: bytes = b"") -> bool:
    secret = _session_secret()
    if secret:
        return verify_internal_v2(secret, request.method, request.url.path, request.headers, body)
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


async def _internal_body(request: Request) -> tuple[dict, JSONResponse | None]:
    raw = await request.body()
    if not _internal_ok(request, raw):
        return {}, JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    return (body if isinstance(body, dict) else {}), None


def _internal_output(value: object) -> str:
    pick = str(value or "").strip() or outputs.STONEPI
    return pick if outputs.valid(pick, browser=False) else outputs.STONEPI


@router.get("/api/internal/now")
def api_now(request: Request, output: str = "", song: str = "", info: str = ""):
    """What's on a panel's output. ``song=1`` (the Car Thing's screensaver) adds the song the
    station is sending, which can take a moment to read; ``info=1`` (the Car Thing's skins) adds
    ``kbps`` and ``khz`` from the stream's headers; ``station_logo`` is the logo's address,
    fetched through /api/internal/art."""
    if not _internal_ok(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    output = _internal_output(output)
    now = _now_safe(output)
    if song and now.get("playing"):
        now = {**now, "song": streams.song_title(outputs.station_url(output))}
    if info and now.get("playing"):
        # The Car Thing's skins: kbps and kHz in their own digits (from the stream's headers).
        # Hold briefly for the first read so the skin's first paint isn't always blank.
        # Prefer the pref, then whatever this output's now() already knows (StonePi JSON, etc.).
        url = outputs.station_url(output) or str(now.get("station_url") or "")
        now = {**now, **streams.stream_info(url, wait_s=2.0)}
    return JSONResponse(now)


@router.get("/api/internal/art")
def api_internal_art(request: Request, u: str = ""):
    """A station logo for the Car Thing (it can't sign in to Radio): the same checks and cache
    as /art, behind the internal signature instead of a session."""
    if not _internal_ok(request):
        return Response(status_code=403)
    found = art.get(u) if art.art_url(u) else None
    if not found:
        return Response(status_code=404)
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.get("/api/internal/outputs")
def api_outputs(request: Request):
    """Speakers + StonePi for Notify's Car Thing editor (no This device: the panel has no browser)."""
    if not _internal_ok(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return JSONResponse({"ok": True, "outputs": outputs.list_outputs(browser=False)})


@router.post("/api/internal/play")
async def api_play(request: Request):
    body, denied = await _internal_body(request)
    if denied:
        return denied
    try:
        target = stations.resolve_play_target(
            owner=None,
            station_id=str(body.get("station_id") or ""),
            url=str(body.get("url") or ""),
            name=str(body.get("name") or ""),
        )
        state = outputs.play(_internal_output(body.get("output")), target)
        state["station"] = target
        state["ok"] = True
        return JSONResponse(state)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/internal/transport")
async def api_transport(request: Request):
    body, denied = await _internal_body(request)
    if denied:
        return denied
    try:
        state = outputs.transport(_internal_output(body.get("output")), str(body.get("action") or ""))
        state["ok"] = True
        return JSONResponse(state)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/internal/volume")
async def api_volume(request: Request):
    body, denied = await _internal_body(request)
    if denied:
        return denied
    output = _internal_output(body.get("output"))
    try:
        if "delta" in body:
            state = outputs.bump_volume(output, float(body.get("delta") or 0))
        else:
            state = outputs.set_volume(
                output, float(body.get("level") if body.get("level") is not None else body.get("volume") or 0.5)
            )
        state["ok"] = True
        return JSONResponse(state)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


# -- Relay: a stream passed on for a speaker or browser that can't fetch it (radio-outputs.md §5) --

_relays = threading.BoundedSemaphore(4)


@router.get("/relay/{token}")
async def relay(token: str):
    import httpx

    found = streams.relay_info(token)
    if not found:
        return Response(status_code=404)
    url = found["url"]
    if not _relays.acquire(blocking=False):
        return Response("Too many streams at once.", status_code=503)
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0), follow_redirects=False,
                               headers={"User-Agent": streams.USER_AGENT})
    if found["hls"]:
        # Speakers can't follow HLS: the relay does, and passes on one continuous AAC stream.
        joined = streams.hls_audio(client, url)
        try:
            first = await joined.__anext__()
        except Exception:
            await joined.aclose()
            await client.aclose()
            _relays.release()
            return Response(status_code=502)

        async def hls_body():
            try:
                yield first
                async for chunk in joined:
                    yield chunk
            except Exception:
                pass  # the station stopped answering: the speaker sees the stream end
            finally:
                await joined.aclose()
                await client.aclose()
                _relays.release()

        return StreamingResponse(hls_body(), media_type=streams.HLS_MIME, headers={"Cache-Control": "no-store"})
    try:
        for _ in range(streams.MAX_HOPS):
            upstream = await client.send(client.build_request("GET", url), stream=True)
            if upstream.is_redirect and upstream.headers.get("location"):
                await upstream.aclose()
                url = stations.check_stream_url(str(upstream.url.join(upstream.headers["location"])))
                continue
            break
        else:
            raise ValueError("too many redirects")
        if upstream.status_code >= 400:
            await upstream.aclose()
            raise ValueError(f"upstream {upstream.status_code}")
    except Exception:
        await client.aclose()
        _relays.release()
        return Response(status_code=502)

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            _relays.release()

    headers = {k: v for k, v in upstream.headers.items() if k.lower().startswith("icy-") and k.lower() != "icy-metaint"}
    headers["Cache-Control"] = "no-store"
    return StreamingResponse(body(), media_type=upstream.headers.get("content-type", "audio/mpeg"), headers=headers)


@router.post("/api/relay")
def relay_for_page(request: Request, csrf_token: str = Form(""), url: str = Form("")):
    """This device couldn't play a stream directly: a relay path for it, same-origin."""
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False, "message": "Sign in again."}, status_code=403)
    if not csrf_ok_request(request, csrf_token):
        return JSONResponse({"ok": False, "message": "Form expired. Refresh the page."}, status_code=403)
    try:
        return JSONResponse({"ok": True, "message": "", "src": streams.relay_path(stations.check_stream_url(url))})
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


# -- Where you listen: the output picker (radio-outputs.md §8) -----------------------------------------


@router.get("/api/outputs")
def outputs_list(request: Request, refresh: str = ""):
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False, "message": "Sign in again."}, status_code=403)
    if refresh:
        dlna.refresh()
    return JSONResponse({"ok": True, "current": outputs.chosen(_owner(user)), "outputs": outputs.list_outputs(browser=True)})


@router.post("/output")
def output_choose(
    request: Request,
    csrf_token: str = Form(""),
    output: str = Form(""),
    station_url: str = Form(""),
    name: str = Form(""),
    favicon: str = Form(""),
    next: str = Form("/now"),
):
    """Switch your output; what was playing moves with you (stops on the old, starts on the new)."""
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _reply(request, False, "Form expired. Refresh the page.", back=next, status=403)
    owner = _owner(user)
    old = outputs.chosen(owner)
    if output == old:
        return _reply(request, True, back=next, output=output)
    if not outputs.valid(output, browser=True):
        return _reply(request, False, "That speaker isn't on the network any more.", back=next)
    # What to carry over: the page says (This device), else the old speaker's current station.
    station = None
    if station_url:
        station = {"stream_url": station_url, "name": name, "favicon": favicon}
    elif old != outputs.BROWSER:
        was = _now_safe(old)
        spk = db.get_speaker(old[len(outputs.DLNA_PREFIX):]) if old.startswith(outputs.DLNA_PREFIX) else None
        url = was.get("station_url") or (spk or {}).get("now_station") or ""
        if was.get("playing") and url:
            station = {"stream_url": url, "name": was.get("title") or "Radio", "favicon": outputs.station_logo(old)}
    # A station the new output can't take (not a stream it can reach): nothing switches, it keeps playing.
    if station:
        try:
            outputs.check_playable(output, station)
        except outputs.OutputError as exc:
            return _reply(request, False, f"{exc} Still playing on {outputs.name_of(old)}.", back=next)
    outputs.choose(owner, output)
    if old != outputs.BROWSER:
        try:
            outputs.transport(old, "stop")
        except Exception:
            pass
    label = outputs.name_of(output)
    if not station:
        return _reply(request, True, f"Playing on {label}", back=next, output=output)
    try:
        if output == outputs.BROWSER:
            return _reply(request, True, f"Playing on {label}", back=next, output=output,
                          browser=_browser_reply(outputs.browser_source(station, https_page=_https_page(request))))
        outputs.play(output, station)
        return _reply(request, True, f"Playing {station['name']} on {label}", back=next, output=output)
    except Exception as exc:
        if old == outputs.BROWSER:
            # Nothing was stopped on This device: go back to it, so the page and the choice agree.
            outputs.choose(owner, old)
            return _reply(request, False, f"{exc} Still playing on {outputs.name_of(old)}.", back=next)
        # The old speaker is already stopped: the new output stays chosen, nothing playing.
        return _reply(request, False, str(exc), back=next, output=output)


@router.get("/", response_class=HTMLResponse)
def listen_page(
    request: Request, show: str = "", q: str = "", add: str = "", msg: str | None = None, error: str | None = None
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if q.strip():
        return RedirectResponse(f"{_prefix()}/discover?q={quote(q.strip())}", status_code=303)
    if show == "favorites":
        # The old "My favourites" view: both lists are on one page now.
        return RedirectResponse(f"{_prefix()}/#favourites", status_code=303)
    # My Radio: Home stations (the household's, admins manage them) and My favourites (your stars).
    owner = _owner(user)
    home = _mark_favorites(db.list_stations(), owner)
    favorites = db.list_favorites(owner)
    listed = db.station_urls()
    for station in favorites:
        station["in_list"] = station["stream_url"] in listed
    return _csrf_response(
        request,
        "listen.html",
        {
            "user": user,
            "active": "listen",
            "home_stations": home,
            "favorites": favorites,
            "section_order": ["favourites", "home"] if _my_radio_first(user) == "favourites" else ["home", "favourites"],
            "show_add": _show_add(user),
            "add_open": bool(add) and _is_admin(user),
            "can_manage": _is_admin(user),
            "now": _now_safe(),
            "message": msg,
            "error": error,
        },
    )


@router.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request, q: str = "", genre: str = "", msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    owner = _owner(user)
    q = q.strip()[:80]
    genre = genre.strip().lower()
    if genre not in discover.GENRE_TAGS:
        genre = ""
    sections: list[dict] = []
    results: list[dict] | None = None
    if q:
        results = stations.browse(q)["stations"]
    elif genre:
        results = discover.genre(genre)
    else:
        sections = discover.sections(owner)
    listed = db.station_urls()
    for group in [results or []] + [sec["stations"] for sec in sections]:
        _mark_favorites(group, owner)
        for station in group:
            station["in_list"] = station["stream_url"] in listed
    return _csrf_response(
        request,
        "discover.html",
        {
            "user": user,
            "active": "discover",
            "query": q,
            "genre": genre,
            "genre_label": discover.GENRE_TAGS.get(genre, ""),
            "genres": discover.GENRES,
            "sections": sections,
            "results": results,
            "can_manage": _is_admin(user),
            "message": msg,
            "error": error,
        },
    )


@router.get("/art/{sig}")
def station_art(request: Request, sig: str, u: str = ""):
    # Logos for signed-in pages only; the signature means only logos Radio linked are fetched.
    if _session_secret() and _user(request) is None:
        return Response(status_code=404)
    if not art.valid(sig, u):
        return Response(status_code=404)
    found = art.get(u)
    if not found:
        return Response(status_code=404, headers={"Cache-Control": "private, max-age=3600"})
    path, media_type = found
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/stations/add")
def station_add(
    request: Request,
    csrf_token: str = Form(""),
    station_id: str = Form(""),
    name: str = Form(""),
    url: str = Form(""),
    favicon: str = Form(""),
    tags: str = Form(""),
    country: str = Form(""),
    homepage: str = Form(""),
    next: str = Form("/"),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect(next or "/", error="Invalid form token.")
    if not _is_admin(user):
        return _redirect(next or "/", error="Only an admin can add stations. Star it to keep it for you.")
    try:
        url = stations.check_stream_url(url)
        favicon = favicon.strip()
        if favicon and not favicon.lower().startswith(("http://", "https://")):
            raise ValueError("The logo must be an http:// or https:// image address.")
        item = db.add_station(
            _owner(user),
            name=name,
            stream_url=url,
            favicon=favicon,
            tags=tags,
            country=country,
            homepage=homepage,
            station_id=station_id or None,
        )
        return _redirect(next or "/", msg=f"Added {item['name']} to Home stations")
    except Exception as exc:
        return _redirect(next or "/", error=str(exc))


@router.post("/stations/remove")
def station_remove(
    request: Request,
    csrf_token: str = Form(""),
    station_id: str = Form(""),
    next: str = Form("/"),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect(next or "/", error="Invalid form token.")
    if not _is_admin(user):
        return _redirect(next or "/", error="Only an admin can remove stations.")
    station = db.get_station(station_id)
    if not station:
        return _redirect(next or "/", error="That station isn't in the list any more.")
    db.remove_station(station_id)
    return _redirect(next or "/", msg=f"Removed {station['name']} from Home stations")


@router.get("/favorites")
def favorites_page():
    return RedirectResponse(f"{_prefix()}/#favourites", status_code=303)


@router.get("/now", response_class=HTMLResponse)
def now_page(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    output = outputs.chosen(_owner(user))
    return _csrf_response(
        request,
        "now.html",
        {
            "user": user,
            "active": "now",
            "now": _now_safe(output),
            "favorites": _presets(user),
            "presets_mode": _presets_mode(user),
            "message": msg,
            "error": error,
        },
    )


@router.post("/play")
def play_form(
    request: Request,
    csrf_token: str = Form(""),
    station_id: str = Form(""),
    url: str = Form(""),
    name: str = Form(""),
    next: str = Form("/now"),
):
    """Play a station on your output. This device: the reply tells the page what to load."""
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _reply(request, False, "Form expired. Refresh the page.", back=next, status=403)
    output = outputs.chosen(_owner(user))
    try:
        target = stations.resolve_play_target(owner=_owner(user), station_id=station_id, url=url, name=name)
        if output == outputs.BROWSER:
            if not _fetched(request):
                return _reply(request, False, "Playing on This device needs JavaScript. Pick a speaker instead.", back=next)
            return _reply(request, True, "", output=output,
                          browser=_browser_reply(outputs.browser_source(target, https_page=_https_page(request))))
        outputs.play(output, target)
        return _reply(request, True, f"Playing {target['name']} on {outputs.name_of(output)}", back=next or "/now", output=output)
    except Exception as exc:
        return _reply(request, False, str(exc), back=next)


@router.post("/transport")
def transport_form(
    request: Request,
    csrf_token: str = Form(""),
    action: str = Form("play_pause"),
    next: str = Form("/now"),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _reply(request, False, "Form expired. Refresh the page.", back=next, status=403)
    output = outputs.chosen(_owner(user))
    if output == outputs.BROWSER:
        return _reply(request, False, "This device is controlled from the player bar.", back=next)
    try:
        state = outputs.transport(output, action)
        return _reply(request, True, back=next, now={k: state.get(k) for k in ("playing", "paused", "playback_status", "title")})
    except Exception as exc:
        return _reply(request, False, str(exc), back=next)


@router.post("/volume")
def volume_form(
    request: Request,
    csrf_token: str = Form(""),
    level: float | None = Form(None),
    delta: float | None = Form(None),
    next: str = Form("/now"),
):
    # The Now page's slider posts here in the background and gets JSON back; − / + and the
    # no-JS Set button post the form and come back to Now, silently. This device's volume
    # never comes here (the page's player sets it).
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False, "message": "Sign in again."}, status_code=403) if _fetched(request) else denied
    if not csrf_ok_request(request, csrf_token):
        return _reply(request, False, "Form expired. Refresh the page.", back=next, status=403)
    output = outputs.chosen(_owner(user))
    if output == outputs.BROWSER:
        return _reply(request, False, "This device's volume is set on the player bar.", back=next)
    try:
        if delta is not None:
            state = outputs.bump_volume(output, max(-0.5, min(0.5, float(delta))))
        else:
            state = outputs.set_volume(output, max(0.0, min(1.0, float(level if level is not None else 0.6))))
        return _reply(request, True, back=next, volume=state.get("volume"))
    except Exception as exc:
        return _reply(request, False, str(exc), back=next)


@router.post("/favorites/add")
def favorite_add(
    request: Request,
    csrf_token: str = Form(""),
    station_id: str = Form(""),
    name: str = Form(""),
    url: str = Form(""),
    favicon: str = Form(""),
    tags: str = Form(""),
    country: str = Form(""),
    next: str = Form("/"),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect(next or "/", error="Invalid form token.")
    try:
        if not url and station_id:
            target = stations.resolve_play_target(owner=_owner(user), station_id=station_id, url=url, name=name)
            name = target["name"]
            url = target["stream_url"]
            favicon = favicon or target.get("favicon") or ""
        url = stations.check_stream_url(url)
        item = db.add_favorite(
            _owner(user),
            name=name,
            stream_url=url,
            favicon=favicon,
            tags=tags,
            country=country,
            station_id=station_id or None,
        )
        return _redirect(next or "/", msg=f"Saved {item['name']}")
    except Exception as exc:
        return _redirect(next or "/", error=str(exc))


@router.post("/favorites/remove")
def favorite_remove(
    request: Request,
    csrf_token: str = Form(""),
    station_id: str = Form(""),
    next: str = Form("/"),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect(next or "/", error="Invalid form token.")
    db.remove_favorite(_owner(user), station_id)
    return _redirect(next or "/", msg="Removed from favorites")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, tab: str | None = None, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    tab = (tab or "").strip().lower()
    admin = _is_admin(user)
    if tab in HOUSEHOLD_TABS and not admin:
        return _redirect("/settings")
    hub = not tab
    title = "Settings"
    lede = "Display and playback, speakers, Odio, and about." if admin else "Your display options, phone alerts and about."
    if tab in {"myradio", "player", "display"}:
        return _redirect("/settings?tab=general")  # all in General now
    odio_status = odio_client().ping()
    from app.services import mympd as mympd_svc

    mympd_status = mympd_svc.ping()
    default_volume = float(db.get_pref("default_volume", str(env.default_volume)) or env.default_volume)
    if tab == "general":
        title, lede = ("General", "Your display options, and the household's playback and Discover." if admin
                       else "How Radio looks for you.")
    elif tab == "speakers":
        title, lede = "Speakers", "DLNA speakers Radio found on the home network. Rename them, or change how Radio drives them."
    elif tab == "odio":
        title, lede = "Odio", "Playback via Odio API; station browse via myMPD WebradioDB (loopback only)."
    elif tab == "notifications":
        title, lede = "Notifications", "Phone alerts for this household."
    elif tab == "about":
        title, lede = "About", "App name, description, GitHub, and version."
    elif tab and tab not in {"general", "speakers", "odio", "notifications", "about"}:
        return _redirect("/settings")

    ctx = {
        "user": user,
        "active": "settings",
        "settings_hub": hub,
        "settings_tab": tab,
        "settings_title": title,
        "settings_lede": lede,
        "settings_groups": tuple(
            (group_id, label, rows)
            for group_id, label, rows in (
                (g, l, tuple(r for r in rs if admin or r[0] not in HOUSEHOLD_TABS)) for g, l, rs in SETTINGS_GROUPS
            )
            if rows
        ),
        "odio_status": odio_status,
        "odio_url_host": env.odio_api_url.split("://", 1)[-1].rstrip("/") if not env.mock else "mock",
        "mympd_status": mympd_status,
        "mympd_url_host": env.mympd_url.split("://", 1)[-1].rstrip("/") if not env.mock else "mock",
        "default_volume": default_volume,
        "player_bar_modes": PLAYER_BAR_MODES,
        "player_positions": PLAYER_POSITIONS,
        "my_radio_first": _my_radio_first(user),
        "my_radio_first_choices": MY_RADIO_FIRST,
        "presets_mode": _presets_mode(user),
        "presets_modes": PRESETS_MODES,
        "show_add": _show_add(user),
        "preset_choices": _preset_choices(_owner(user)) if tab == "general" else [],
        "chosen_presets": _chosen_preset_urls(user),
        "can_edit_household": _is_admin(user),
        "speakers": db.list_speakers(time.time()) if tab == "speakers" else [],
        "country": db.get_pref("country", ""),
        "country_auto": discover.country_from_timezone(),
        "countries": discover.countries() if tab == "general" else [],
        "message": msg,
        "error": error,
        "notifications_card_state": notifications_card_context(
            "radio",
            user if _session_secret() else None,
            home_url=portal_home_url(request, env.public_origin).rstrip("/"),
        )
        if tab == "notifications"
        else None,
    }
    return _csrf_response(request, "settings.html", ctx)


def _save_display(user, form) -> str:
    """Each person's own display options from Settings → General; an error message, or ""."""
    first = str(form.get("first") or "home")
    bar = str(form.get("mode") or "device")
    presets_mode = str(form.get("presets_mode") or "latest")
    pos = str(form.get("bar_pos") or "bottom-left")
    if (first not in {m[0] for m in MY_RADIO_FIRST} or bar not in {m[0] for m in PLAYER_BAR_MODES}
            or presets_mode not in {m[0] for m in PRESETS_MODES} or pos not in {p[0] for p in PLAYER_POSITIONS}):
        return "Pick one of the choices."
    owner = _owner(user)
    allowed = {s["stream_url"] for s in _preset_choices(owner)}
    picks = [str(form.get(f"preset_{n}") or "") for n in range(1, PRESET_COUNT + 1)]
    if any(u and u not in allowed for u in picks):
        return "A preset must be one of your favourites or a Home station."
    db.set_person_pref(owner, MY_RADIO_FIRST_KEY, first)
    db.set_person_pref(owner, PLAYER_BAR_KEY, bar)
    db.set_person_pref(owner, PLAYER_POS_KEY, pos)
    db.set_person_pref(owner, PRESETS_MODE_KEY, presets_mode)
    db.set_person_pref(owner, PRESETS_KEY, json.dumps(picks))
    if _is_admin(user) and form.get("show_add_field"):  # the checkbox is only on admins' form
        db.set_person_pref(owner, SHOW_ADD_KEY, "1" if form.get("show_add") else "0")
    return ""


@router.get("/api/now")
def api_now_for_page(request: Request):
    """What's playing on your output, for the player bar (Wherever I play)."""
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False, "message": "Sign in again."}, status_code=403)
    output = outputs.chosen(_owner(user))
    now = _now_safe(output)
    keep = ("playing", "paused", "playback_status", "title", "artist", "output_name", "connected")
    logo = art.art_url(now.get("station_logo") or "") if now.get("title") else ""
    return JSONResponse({"ok": True, "output": output, "art": logo or "", **{k: now.get(k) for k in keep}},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/song")
def api_song(request: Request, url: str = ""):
    """The song a station is sending (ICY metadata), for Now Playing and the player bar. ``url``:
    the station This device is playing; without it, the one on your speaker or StonePi."""
    user, denied = _require_user(request)
    if denied:
        return JSONResponse({"ok": False, "message": "Sign in again."}, status_code=403)
    if not url:
        output = outputs.chosen(_owner(user))
        url = outputs.station_url(output) if output != outputs.BROWSER else ""
    try:
        url = stations.check_stream_url(url) if url else ""
    except ValueError:
        url = ""
    return JSONResponse({"ok": True, "song": streams.song_title(url) if url else ""},
                        headers={"Cache-Control": "no-store"})


def _browser_reply(source: dict) -> dict:
    """What This device loads, with the station's logo as a signed /art link (the bar shows it)."""
    return {**source, "art": art.art_url(source.get("favicon") or "") or ""}


@router.post("/settings/general")
async def settings_general(request: Request):
    """Settings → General: your display options (anyone), and for admins the household's
    playback (default volume) and Discover (country)."""
    user, denied = _require_user(request)
    if denied:
        return denied
    form = await request.form()
    if not csrf_ok_request(request, str(form.get("csrf_token") or "")):
        return _redirect("/settings?tab=general", error="Invalid form token.")
    error = _save_display(user, form)
    if error:
        return _redirect("/settings?tab=general", error=error)
    # Household fields are only on admins' form; a member's post never changes them.
    if _is_admin(user) and form.get("default_volume") is not None:
        try:
            level = max(0.0, min(1.0, float(form.get("default_volume") or 0.6)))
        except ValueError:
            return _redirect("/settings?tab=general", error="The default volume must be a number.")
        db.set_pref("default_volume", f"{level:.2f}")
        country = str(form.get("country") or "").strip().upper()
        if country == "-" or (len(country) == 2 and country.isalpha()):
            db.set_pref("country", country)
        elif not country:
            db.set_pref("country", "")  # back to the Pi's time zone
    return _redirect("/settings?tab=general", msg="Saved")


@router.post("/settings/speakers/refresh")
def settings_speakers_refresh(request: Request, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect("/settings?tab=speakers", error="Invalid form token.")
    if not _is_admin(user):
        return _redirect("/settings", error="Only an admin can change household settings.")
    found = dlna.refresh(force=True)
    return _redirect("/settings?tab=speakers", msg=f"Found {found} speaker{'s' if found != 1 else ''}")


@router.post("/settings/speakers/{udn}")
def settings_speaker_save(
    request: Request,
    udn: str,
    csrf_token: str = Form(""),
    label: str = Form(""),
    use_linkplay: str = Form(""),
    relay_https: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok_request(request, csrf_token):
        return _redirect("/settings?tab=speakers", error="Invalid form token.")
    if not _is_admin(user):
        return _redirect("/settings", error="Only an admin can change household settings.")
    spk = db.get_speaker(udn)
    if not spk:
        return _redirect("/settings?tab=speakers", error="That speaker isn't known any more.")
    db.update_speaker(
        udn,
        label=label.strip()[:60],
        use_linkplay=bool(use_linkplay) and spk["linkplay"],
        # The switch reads "Pass https stations through the Pi" (on, the default, is the relay);
        # stored the other way round, as whether the speaker plays https itself.
        direct_https=not relay_https,
    )
    return _redirect("/settings?tab=speakers", msg=f"Saved {label.strip() or spk['name']}")


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not csrf_ok_request(request, csrf_token):
        return _redirect("/", error="Invalid form token.")
    response = RedirectResponse(logout_url(_settings()), status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
