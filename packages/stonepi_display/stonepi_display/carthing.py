"""Car Thing panel: config schema, saved configs and device state (owned by Notify).

The Car Thing (Spotify "superbird", flashed with the Wall Thing Chromium kiosk)
plugs into the Pi by USB and loads the panel served by ``apps/carthing``. Notify
edits everything here; the panel service only reads it (signed internal API).

Layout under Notify's data dir::

    carthing/device.json          enabled, pairing token hash, PIN, active config id
    carthing/configs/<id>.json    saved configs (one is active)
    carthing/snapshots/<id>/      last N versions of each config, written on save
    carthing/assets/<id>.jpg      uploaded backgrounds (800x480)

Configs are kept separate from ``displays.json`` so TRMNL normalisation, grids
and pushes never see a Car Thing.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
MAX_CONFIGS = 20
MAX_SNAPSHOTS = 10
PIN_LENGTH = 4

_data_dir: Path | None = None
_lock = threading.RLock()

# ── Catalogues ───────────────────────────────────────────────────────────────

# Physical controls (+ long presses) and touch gestures. Order = editor order.
INPUTS: tuple[dict[str, str], ...] = (
    {"id": "preset1", "label": "Preset 1", "kind": "button"},
    {"id": "preset2", "label": "Preset 2", "kind": "button"},
    {"id": "preset3", "label": "Preset 3", "kind": "button"},
    {"id": "preset4", "label": "Preset 4", "kind": "button"},
    {"id": "m", "label": "M button", "kind": "button"},
    {"id": "back", "label": "Back", "kind": "button"},
    {"id": "dial_cw", "label": "Dial clockwise", "kind": "dial"},
    {"id": "dial_ccw", "label": "Dial anticlockwise", "kind": "dial"},
    {"id": "dial_press", "label": "Dial press", "kind": "dial"},
    {"id": "preset1_long", "label": "Preset 1 (hold)", "kind": "long"},
    {"id": "preset2_long", "label": "Preset 2 (hold)", "kind": "long"},
    {"id": "preset3_long", "label": "Preset 3 (hold)", "kind": "long"},
    {"id": "preset4_long", "label": "Preset 4 (hold)", "kind": "long"},
    {"id": "m_long", "label": "M button (hold)", "kind": "long"},
    {"id": "back_long", "label": "Back (hold)", "kind": "long"},
    {"id": "dial_press_long", "label": "Dial press (hold)", "kind": "long"},
    {"id": "swipe_left", "label": "Swipe left", "kind": "touch"},
    {"id": "swipe_right", "label": "Swipe right", "kind": "touch"},
    {"id": "swipe_up", "label": "Swipe up", "kind": "touch"},
    {"id": "swipe_down", "label": "Swipe down", "kind": "touch"},
)
INPUT_IDS = tuple(i["id"] for i in INPUTS)

# Lightweight views on the panel. ``source`` = app whose /api/display?items=N feeds it.
MINI_APPS: tuple[dict[str, str], ...] = (
    {"id": "system", "label": "System", "source": "notify"},
    {"id": "sportguide", "label": "SportGuide", "source": "sportguide"},
    {"id": "eventtrakr", "label": "EventTrakr", "source": "eventtrakr"},
    {"id": "newscast", "label": "NewsCast", "source": "newscast"},
    {"id": "pinboard", "label": "Pinboard", "source": "pinboard"},
)
MINI_APP_IDS = tuple(a["id"] for a in MINI_APPS)

# What a page can hold: every mini-app (its card) plus the clock & weather widget.
WIDGETS: tuple[dict[str, str], ...] = (
    *MINI_APPS,
    {"id": "clock", "label": "Clock & weather", "source": ""},
)
WIDGET_IDS = tuple(w["id"] for w in WIDGETS)
# Sizes on an 800x480 page: small = today's card, half = one half of the page, full = whole page.
# A page holds PAGE_CAPACITY "spaces": up to 6 small, a half + 3 small, two halves, or one full.
WIDGET_SIZES: tuple[dict[str, Any], ...] = (
    {"id": "small", "label": "Small", "cost": 1},
    {"id": "half", "label": "Half page", "cost": 3},
    {"id": "full", "label": "Full page", "cost": 6},
)
WIDGET_SIZE_IDS = tuple(s["id"] for s in WIDGET_SIZES)
SIZE_COST = {s["id"]: s["cost"] for s in WIDGET_SIZES}
PAGE_CAPACITY = 6
MAX_PAGES = 6
CONFIG_SCHEMA = 2  # 1 = a single ``home``; 2 = ``pages`` + ``rotation``

_NAV_ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "home", "label": "Home", "group": "Navigate"},
    {"id": "back", "label": "Back", "group": "Navigate"},
    {"id": "next", "label": "Next item", "group": "Navigate"},
    {"id": "prev", "label": "Previous item", "group": "Navigate"},
    {"id": "select", "label": "Select", "group": "Navigate"},
    {"id": "next_app", "label": "Next mini-app", "group": "Navigate"},
    {"id": "prev_app", "label": "Previous mini-app", "group": "Navigate"},
)
_PAGE_ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "next_page", "label": "Next page", "group": "Pages"},
    {"id": "prev_page", "label": "Previous page", "group": "Pages"},
    *({"id": f"page:{n}", "label": f"Go to page {n}", "group": "Pages"} for n in range(1, MAX_PAGES + 1)),
)
PAGE_ACTION_IDS = tuple(a["id"] for a in _PAGE_ACTIONS)
_DEVICE_ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "screensaver", "label": "Screensaver now", "group": "Screen"},
    {"id": "brightness_up", "label": "Brighter", "group": "Screen"},
    {"id": "brightness_down", "label": "Dimmer", "group": "Screen"},
    {"id": "restart", "label": "Restart a service", "group": "Admin"},
)
ACTIONS: tuple[dict[str, str], ...] = (
    *_NAV_ACTIONS,
    *_PAGE_ACTIONS,
    *({"id": f"open:{a['id']}", "label": f"Open {a['label']}", "group": "Mini-apps"} for a in MINI_APPS),
    *_DEVICE_ACTIONS,
)
ACTION_IDS = tuple(a["id"] for a in ACTIONS)

CLOCK_FACES: tuple[dict[str, str], ...] = (
    {"id": "digital_bold", "label": "Bold digital"},
    {"id": "digital_thin", "label": "Thin digital"},
    {"id": "segment", "label": "Segment LCD"},
    {"id": "stacked", "label": "Stacked"},
    {"id": "flip", "label": "Split-flap"},
    {"id": "analogue", "label": "Analogue"},
    {"id": "words", "label": "Word clock"},
)
CLOCK_FACE_IDS = tuple(f["id"] for f in CLOCK_FACES)

COLOR_PRESETS: tuple[dict[str, str], ...] = (
    {"id": "cream", "label": "Cream", "value": "#F5F1E8"},
    {"id": "white", "label": "White", "value": "#FFFFFF"},
    {"id": "amber", "label": "Amber", "value": "#FFB347"},
    {"id": "orange", "label": "Orange", "value": "#FF7A1A"},
    {"id": "mint", "label": "Mint", "value": "#7FE0B0"},
    {"id": "ice", "label": "Ice", "value": "#9FD8FF"},
    {"id": "red", "label": "Red", "value": "#FF4D4D"},
)

# Static gradients only (no animation: the device composites in software).
GRADIENTS: tuple[dict[str, str], ...] = (
    {"id": "midnight", "label": "Midnight", "css": "linear-gradient(135deg,#0b1020 0%,#1b2440 100%)"},
    {"id": "ember", "label": "Ember", "css": "linear-gradient(135deg,#1a0d06 0%,#3d1a08 100%)"},
    {"id": "forest", "label": "Forest", "css": "linear-gradient(135deg,#07140d 0%,#14301f 100%)"},
    {"id": "slate", "label": "Slate", "css": "linear-gradient(135deg,#111316 0%,#2a2f36 100%)"},
    {"id": "dusk", "label": "Dusk", "css": "linear-gradient(135deg,#140b1f 0%,#33183d 100%)"},
)
GRADIENT_IDS = tuple(g["id"] for g in GRADIENTS)
BACKGROUND_TYPES = ("solid", "gradient", "image")
LIVE_LINES = ("none", "eventtrakr", "sportguide", "newscast", "pinboard")
QUIET_MODES = ("dim", "off")
HOUR_FORMATS = ("24", "12")
# Formatted on the device (panel.js) so the date follows the screen's clock tick.
DATE_STYLES = {
    "ddd d MMM": "Fri 3 Oct",
    "dddd d MMMM": "Friday 3 October",
    "d/M": "3/10",
    "yyyy-MM-dd": "2026-10-03",
}
THEMES = ("dark", "light")
UNITS = ("metric", "imperial")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ASSET = re.compile(r"^[a-f0-9]{16}$")

DEFAULT_CONTROLS: dict[str, str | None] = {
    "preset1": "open:newscast",
    "preset2": "open:sportguide",
    "preset3": "open:eventtrakr",
    "preset4": "open:pinboard",
    "m": "home",
    "back": "back",
    "dial_cw": "next",
    "dial_ccw": "prev",
    "dial_press": "select",
    "preset1_long": None,
    "preset2_long": None,
    "preset3_long": None,
    "preset4_long": None,
    "m_long": "screensaver",
    "back_long": "home",
    "dial_press_long": None,
    "swipe_left": "next_app",
    "swipe_right": "prev_app",
    "swipe_up": None,
    "swipe_down": "screensaver",
}


def default_config(config_id: str = "default", name: str = "Default") -> dict[str, Any]:
    return {
        "id": config_id,
        "name": name,
        "updated": None,
        "schema": CONFIG_SCHEMA,
        "pages": [
            {
                "name": "Home",
                "widgets": [
                    {"id": w, "size": "small"} for w in ("system", "sportguide", "eventtrakr", "newscast", "pinboard")
                ],
            }
        ],
        # every_s = 0: pages only change by hand. Rotation pauses while someone uses the panel.
        "rotation": {"every_s": 0, "resume_after_s": 30},
        "allowed_actions": list(ACTION_IDS),
        "controls": dict(DEFAULT_CONTROLS),
        "idle": {
            "screensaver_after_s": 60,
            "dim_after_s": 300,
            "dim_level": 25,
            "quiet_hours": {"enabled": True, "from": "22:30", "to": "06:30", "mode": "dim", "level": 5},
            "background": {"type": "gradient", "value": "midnight"},
            "show": {"date": True, "weather": True, "live_line": "eventtrakr"},
        },
        "clock": {
            "face": "digital_bold",
            "color": "#F5F1E8",
            "accent": "#FF7A1A",
            "quiet_color": None,
            "hour_format": "24",
            "seconds": False,
            "date_format": "ddd d MMM",
        },
        "look": {"theme": "dark", "accent": "#FF7A1A", "units": "metric"},
        "weather": {"lat": None, "lon": None, "label": ""},
    }


# ── Normalisation ────────────────────────────────────────────────────────────


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _color(value: Any, default: str | None) -> str | None:
    text = str(value or "").strip()
    return text.upper() if _HEX.match(text) else default


def _choice(value: Any, options: tuple[str, ...] | list[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in options else default


def _time(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if _TIME.match(text) else default


def _coord(value: Any, lo: float, hi: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if lo <= number <= hi else None


def clean_config_id(value: Any) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")[:40]
    return text if _ID.match(text or "") else ""


def _normalize_widget(item: Any) -> dict[str, str] | None:
    """``"newscast"`` (schema 1) or ``{"id": "newscast", "size": "half"}``."""
    if isinstance(item, dict):
        wid, size = str(item.get("id") or "").strip(), str(item.get("size") or "").strip()
    else:
        wid, size = str(item or "").strip(), "small"
    if wid not in WIDGET_IDS:
        return None
    return {"id": wid, "size": size if size in WIDGET_SIZE_IDS else "small"}


def _normalize_pages(raw: Any) -> list[dict[str, Any]]:
    """Ordered pages; each keeps the widgets that fit its spaces (in order, no repeats)."""
    pages: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if len(pages) >= MAX_PAGES:
            break
        if not isinstance(entry, dict):
            continue
        widgets: list[dict[str, str]] = []
        used = 0
        for item in entry.get("widgets") if isinstance(entry.get("widgets"), list) else []:
            widget = _normalize_widget(item)
            if widget is None or any(w["id"] == widget["id"] for w in widgets):
                continue
            cost = SIZE_COST[widget["size"]]
            if used + cost > PAGE_CAPACITY:
                continue
            used += cost
            widgets.append(widget)
        name = str(entry.get("name") or "").strip()[:30] or ("Home" if not pages else f"Page {len(pages) + 1}")
        pages.append({"name": name, "widgets": widgets})
    return pages or [{"name": "Home", "widgets": []}]


def page_widget_ids(config: dict[str, Any]) -> list[str]:
    """Every widget on any page, in first-seen order (feeds to fetch, mini-apps to cycle)."""
    seen: list[str] = []
    for page in config.get("pages") or []:
        for widget in page.get("widgets") or []:
            if widget.get("id") not in seen:
                seen.append(widget["id"])
    return seen


def page_layout(widgets: list[dict[str, Any]]) -> dict[str, Any]:
    """Where a page's widgets sit on the 800x480 screen.

    - ``full``: one full-page widget.
    - ``split``: two columns; a half widget fills one, small ones stack in the other.
    - ``grid``: small cards only (1-6), the original Home grid.
    """
    widgets = [w for w in widgets if isinstance(w, dict)]
    full = next((w for w in widgets if w.get("size") == "full"), None)
    if full is not None:
        return {"kind": "full", "cols": [[full]]}
    if not any(w.get("size") == "half" for w in widgets):
        return {"kind": "grid", "cols": [widgets[:PAGE_CAPACITY]]}
    cols: list[list[dict[str, Any]]] = []
    smalls: list[dict[str, Any]] = []
    for widget in widgets:
        if widget.get("size") == "half":
            cols.append([widget])
        else:
            if not smalls:
                cols.append(smalls)  # the stacked column sits where its first card comes
            smalls.append(widget)
    return {"kind": "split", "cols": cols[:2]}


def normalize_config(raw: Any) -> dict[str, Any]:
    """Coerce anything (form JSON, an import, an old file) into a valid config."""
    data = raw if isinstance(raw, dict) else {}
    base = default_config()
    out = copy.deepcopy(base)
    out["id"] = clean_config_id(data.get("id")) or base["id"]
    out["name"] = (str(data.get("name") or "").strip() or out["id"].replace("-", " ").title())[:60]
    out["updated"] = str(data["updated"]) if data.get("updated") else None
    out["schema"] = CONFIG_SCHEMA

    pages_raw = data.get("pages")
    legacy = not isinstance(pages_raw, list)
    if legacy:
        # Schema 1 (one Home screen): its cards become page 1, all small as before.
        home = data.get("home") if isinstance(data.get("home"), dict) else {}
        if isinstance(home.get("widgets"), list):
            pages_raw = [{"name": "Home", "widgets": home["widgets"]}]
        else:
            pages_raw = base["pages"]
    out["pages"] = _normalize_pages(pages_raw)
    rotation_raw = data.get("rotation") if isinstance(data.get("rotation"), dict) else {}
    every = _int(rotation_raw.get("every_s"), 0, 0, 3600)
    out["rotation"] = {
        "every_s": every if every == 0 else max(5, every),
        "resume_after_s": _int(rotation_raw.get("resume_after_s"), 30, 5, 3600),
    }

    allowed_raw = data.get("allowed_actions")
    if isinstance(allowed_raw, list):
        allowed_set = {str(a) for a in allowed_raw}
        # Back and Home always work: without them the panel can strand you.
        allowed_set |= {"home", "back"}
        if legacy:
            # Page actions didn't exist yet: an old allow-list mustn't silently forbid them.
            allowed_set |= set(PAGE_ACTION_IDS)
        out["allowed_actions"] = [a for a in ACTION_IDS if a in allowed_set]
    allowed = set(out["allowed_actions"])

    controls_raw = data.get("controls") if isinstance(data.get("controls"), dict) else None
    controls: dict[str, str | None] = {}
    for input_id in INPUT_IDS:
        if controls_raw is None:
            action = DEFAULT_CONTROLS.get(input_id)
        else:
            action = controls_raw.get(input_id)
            action = str(action).strip() if action else None
        controls[input_id] = action if action in allowed else None
    out["controls"] = controls

    idle_raw = data.get("idle") if isinstance(data.get("idle"), dict) else {}
    idle = out["idle"]
    saver_after = _int(idle_raw.get("screensaver_after_s"), idle["screensaver_after_s"], 0, 3600)
    # 0 = never (a rotating dashboard); quiet hours still bring the screensaver.
    idle["screensaver_after_s"] = saver_after if saver_after == 0 else max(10, saver_after)
    idle["dim_after_s"] = _int(idle_raw.get("dim_after_s"), idle["dim_after_s"], 0, 21600)
    idle["dim_level"] = _int(idle_raw.get("dim_level"), idle["dim_level"], 5, 100)
    quiet_raw = idle_raw.get("quiet_hours") if isinstance(idle_raw.get("quiet_hours"), dict) else {}
    quiet = idle["quiet_hours"]
    if "enabled" in quiet_raw:
        quiet["enabled"] = bool(quiet_raw.get("enabled"))
    quiet["from"] = _time(quiet_raw.get("from"), quiet["from"])
    quiet["to"] = _time(quiet_raw.get("to"), quiet["to"])
    quiet["mode"] = _choice(quiet_raw.get("mode"), QUIET_MODES, quiet["mode"])
    quiet["level"] = _int(quiet_raw.get("level"), quiet["level"], 1, 100)
    bg_raw = idle_raw.get("background") if isinstance(idle_raw.get("background"), dict) else {}
    bg_type = _choice(bg_raw.get("type"), BACKGROUND_TYPES, idle["background"]["type"])
    bg_value = str(bg_raw.get("value") or "").strip()
    if bg_type == "solid":
        bg_value = _color(bg_value, "#101114") or "#101114"
    elif bg_type == "image":
        if not _ASSET.match(bg_value):
            bg_type, bg_value = "gradient", "midnight"
    else:
        bg_value = bg_value if bg_value in GRADIENT_IDS else "midnight"
    idle["background"] = {"type": bg_type, "value": bg_value}
    show_raw = idle_raw.get("show") if isinstance(idle_raw.get("show"), dict) else {}
    show = idle["show"]
    for key in ("date", "weather"):
        if key in show_raw:
            show[key] = bool(show_raw.get(key))
    show["live_line"] = _choice(show_raw.get("live_line"), LIVE_LINES, show["live_line"])

    clock_raw = data.get("clock") if isinstance(data.get("clock"), dict) else {}
    clock = out["clock"]
    clock["face"] = _choice(clock_raw.get("face"), CLOCK_FACE_IDS, clock["face"])
    clock["color"] = _color(clock_raw.get("color"), clock["color"])
    clock["accent"] = _color(clock_raw.get("accent"), clock["accent"])
    clock["quiet_color"] = _color(clock_raw.get("quiet_color"), None)
    clock["hour_format"] = _choice(clock_raw.get("hour_format"), HOUR_FORMATS, clock["hour_format"])
    if "seconds" in clock_raw:
        clock["seconds"] = bool(clock_raw.get("seconds"))
    clock["date_format"] = _choice(clock_raw.get("date_format"), tuple(DATE_STYLES), clock["date_format"])

    look_raw = data.get("look") if isinstance(data.get("look"), dict) else {}
    look = out["look"]
    look["theme"] = _choice(look_raw.get("theme"), THEMES, look["theme"])
    look["accent"] = _color(look_raw.get("accent"), look["accent"])
    look["units"] = _choice(look_raw.get("units"), UNITS, look["units"])

    weather_raw = data.get("weather") if isinstance(data.get("weather"), dict) else {}
    lat = _coord(weather_raw.get("lat"), -90, 90)
    lon = _coord(weather_raw.get("lon"), -180, 180)
    out["weather"] = {
        "lat": lat if lon is not None else None,
        "lon": lon if lat is not None else None,
        "label": str(weather_raw.get("label") or "").strip()[:60],
    }
    return out


# ── Device record: token + PIN ───────────────────────────────────────────────

DEVICE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "token_hash": "",
    "paired_at": None,
    "active_config": "default",
    "pin": {"enabled": False, "hash": "", "salt": "", "max_attempts": 5, "lockout_s": 300},
    "last_seen": None,
}


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def token_ok(token: str, token_hash: str) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def _pin_digest(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()


def valid_pin(pin: str) -> bool:
    return bool(re.fullmatch(rf"\d{{{PIN_LENGTH}}}", str(pin or "")))


def pin_ok(pin: str, pin_record: dict[str, Any]) -> bool:
    salt = str(pin_record.get("salt") or "")
    stored = str(pin_record.get("hash") or "")
    if not valid_pin(pin) or not salt or not stored:
        return False
    try:
        return hmac.compare_digest(_pin_digest(pin, salt), stored)
    except ValueError:
        return False


def _normalize_device(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(DEVICE_DEFAULTS)
    out["enabled"] = bool(data.get("enabled"))
    out["token_hash"] = str(data.get("token_hash") or "")
    out["paired_at"] = data.get("paired_at") or None
    out["active_config"] = clean_config_id(data.get("active_config")) or "default"
    out["last_seen"] = data.get("last_seen") or None
    pin_raw = data.get("pin") if isinstance(data.get("pin"), dict) else {}
    pin = out["pin"]
    pin["hash"] = str(pin_raw.get("hash") or "")
    pin["salt"] = str(pin_raw.get("salt") or "")
    # A PIN can only be on when one is set.
    pin["enabled"] = bool(pin_raw.get("enabled")) and bool(pin["hash"] and pin["salt"])
    pin["max_attempts"] = _int(pin_raw.get("max_attempts"), 5, 3, 10)
    pin["lockout_s"] = _int(pin_raw.get("lockout_s"), 300, 30, 3600)
    return out


# ── Store ────────────────────────────────────────────────────────────────────


def configure_carthing(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def _root() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_display carthing not configured")
    return _data_dir / "carthing"


def assets_dir() -> Path:
    return _root() / "assets"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_device() -> dict[str, Any]:
    with _lock:
        return _normalize_device(_read_json(_root() / "device.json"))


def save_device(device: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        normalized = _normalize_device(device)
        _write_json(_root() / "device.json", normalized)
        return normalized


def update_device(**fields: Any) -> dict[str, Any]:
    with _lock:
        device = load_device()
        device.update(fields)
        return save_device(device)


def new_pairing_token() -> str:
    """Mint a fresh device token (old one stops working). Returns the plaintext once."""
    token = secrets.token_urlsafe(24)
    update_device(token_hash=hash_token(token), paired_at=_now())
    return token


def set_pin(pin: str | None, *, enabled: bool) -> dict[str, Any]:
    """Set/replace the PIN (when ``pin`` given) and switch it on or off."""
    with _lock:
        device = load_device()
        record = device["pin"]
        if pin:
            if not valid_pin(pin):
                raise ValueError(f"The PIN must be {PIN_LENGTH} digits.")
            salt = secrets.token_hex(16)
            record.update(salt=salt, hash=_pin_digest(pin, salt))
        if enabled and not record["hash"]:
            raise ValueError("Set a PIN before switching it on.")
        record["enabled"] = bool(enabled)
        return save_device(device)


def _configs_dir() -> Path:
    return _root() / "configs"


def _config_path(config_id: str) -> Path:
    cid = clean_config_id(config_id)
    if not cid:
        raise ValueError("invalid config id")
    return _configs_dir() / f"{cid}.json"


def list_configs() -> list[dict[str, Any]]:
    """Saved configs (id, name, updated), active first. Seeds the default on first use."""
    with _lock:
        folder = _configs_dir()
        if not folder.is_dir() or not any(folder.glob("*.json")):
            save_config(default_config(), snapshot=False)
        active = load_device()["active_config"]
        rows = []
        for path in sorted(folder.glob("*.json")):
            cfg = normalize_config(_read_json(path))
            if cfg["id"] != path.stem:
                continue
            rows.append({"id": cfg["id"], "name": cfg["name"], "updated": cfg["updated"], "active": cfg["id"] == active})
        rows.sort(key=lambda r: (not r["active"], r["name"].lower()))
        return rows


def get_config(config_id: str) -> dict[str, Any] | None:
    with _lock:
        try:
            path = _config_path(config_id)
        except ValueError:
            return None
        raw = _read_json(path)
        if raw is None:
            return None
        cfg = normalize_config(raw)
        cfg["id"] = path.stem
        return cfg


def active_config() -> dict[str, Any]:
    """The config the device shows. Falls back to any saved one, then to defaults."""
    with _lock:
        rows = list_configs()
        device = load_device()
        cfg = get_config(device["active_config"])
        if cfg is None and rows:
            cfg = get_config(rows[0]["id"])
        return cfg or default_config()


def save_config(config: dict[str, Any], *, snapshot: bool = True) -> dict[str, Any]:
    """Write a config; the previous version goes to snapshots (last ``MAX_SNAPSHOTS``)."""
    with _lock:
        cfg = normalize_config(config)
        path = _config_path(cfg["id"])
        if not path.exists() and len(list(_configs_dir().glob("*.json"))) >= MAX_CONFIGS:
            raise ValueError(f"At most {MAX_CONFIGS} saved configs.")
        if snapshot and path.exists():
            _snapshot(cfg["id"], path)
        cfg["updated"] = _now()
        _write_json(path, cfg)
        return cfg


def _snapshot(config_id: str, path: Path) -> None:
    folder = _root() / "snapshots" / config_id
    folder.mkdir(parents=True, exist_ok=True)
    # Coarse clocks (Windows ~15 ms) can repeat a timestamp; step on so saves never overwrite.
    when = datetime.now(timezone.utc)
    target = folder / f"{when.strftime('%Y%m%dT%H%M%S%f')}.json"
    while target.exists():
        when += timedelta(microseconds=1)
        target = folder / f"{when.strftime('%Y%m%dT%H%M%S%f')}.json"
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    for old in sorted(folder.glob("*.json"))[:-MAX_SNAPSHOTS]:
        old.unlink(missing_ok=True)


def list_snapshots(config_id: str) -> list[dict[str, Any]]:
    cid = clean_config_id(config_id)
    folder = _root() / "snapshots" / cid
    if not cid or not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        cfg = normalize_config(_read_json(path))
        rows.append({"id": path.stem, "name": cfg["name"], "updated": cfg["updated"]})
    return rows


def restore_snapshot(config_id: str, snapshot_id: str) -> dict[str, Any]:
    cid = clean_config_id(config_id)
    if not cid or not re.fullmatch(r"\d{8}T\d{12}", str(snapshot_id or "")):
        raise ValueError("Unknown snapshot.")
    path = _root() / "snapshots" / cid / f"{snapshot_id}.json"
    raw = _read_json(path)
    if raw is None:
        raise ValueError("Unknown snapshot.")
    cfg = normalize_config(raw)
    cfg["id"] = cid
    return save_config(cfg)


def unique_config_id(name: str) -> str:
    base = clean_config_id(name) or "config"
    candidate, n = base, 2
    while _config_path(candidate).exists():
        candidate = f"{base[:36]}-{n}"
        n += 1
    return candidate


def copy_config(config_id: str, name: str) -> dict[str, Any]:
    with _lock:
        source = get_config(config_id)
        if source is None:
            raise ValueError("Unknown config.")
        new_name = (name or f"{source['name']} copy").strip()[:60]
        clone = {**source, "id": unique_config_id(new_name), "name": new_name}
        return save_config(clone, snapshot=False)


def rename_config(config_id: str, name: str) -> dict[str, Any]:
    with _lock:
        cfg = get_config(config_id)
        if cfg is None:
            raise ValueError("Unknown config.")
        cfg["name"] = (name or cfg["name"]).strip()[:60] or cfg["name"]
        return save_config(cfg, snapshot=False)


def delete_config(config_id: str) -> None:
    with _lock:
        if load_device()["active_config"] == clean_config_id(config_id):
            raise ValueError("The active config can't be deleted — activate another first.")
        path = _config_path(config_id)
        path.unlink(missing_ok=True)


def activate_config(config_id: str) -> dict[str, Any]:
    with _lock:
        cfg = get_config(config_id)
        if cfg is None:
            raise ValueError("Unknown config.")
        update_device(active_config=cfg["id"])
        return cfg


def import_config(raw: Any, *, name: str = "") -> dict[str, Any]:
    """Import a config (exported JSON) as a new saved config — never overwrites."""
    if not isinstance(raw, dict):
        raise ValueError("That file isn't a Car Thing config.")
    cfg = normalize_config(raw)
    cfg["name"] = (name or cfg["name"]).strip()[:60] or "Imported"
    cfg["id"] = unique_config_id(cfg["name"])
    return save_config(cfg, snapshot=False)


def export_config(config_id: str) -> dict[str, Any] | None:
    cfg = get_config(config_id)
    if cfg is None:
        return None
    # Format marker: 2 = pages. Imports also take 1 (single Home), migrated on read.
    return {"stonepi_carthing_config": CONFIG_SCHEMA, **cfg}


def state_rev(config: dict[str, Any], device: dict[str, Any]) -> str:
    """Short fingerprint the panel compares to decide whether to reload."""
    blob = json.dumps(
        {"c": config, "pin": device["pin"].get("enabled"), "t": device.get("token_hash")}, sort_keys=True
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def public_state() -> dict[str, Any]:
    """What the panel service needs: active config + device auth material + rev."""
    device = load_device()
    config = active_config()
    return {
        "ok": True,
        "enabled": device["enabled"],
        "token_hash": device["token_hash"],
        "pin": {k: device["pin"][k] for k in ("enabled", "hash", "salt", "max_attempts", "lockout_s")},
        "config": config,
        "rev": state_rev(config, device),
    }


def editor_catalog() -> dict[str, Any]:
    """Everything the Notify editor needs to build its controls (JSON-ready)."""
    return {
        "inputs": [dict(i) for i in INPUTS],
        "actions": [dict(a) for a in ACTIONS],
        "mini_apps": [dict(a) for a in MINI_APPS],
        "widgets": [dict(w) for w in WIDGETS],
        "widget_sizes": [dict(z) for z in WIDGET_SIZES],
        "page_capacity": PAGE_CAPACITY,
        "max_pages": MAX_PAGES,
        "clock_faces": [dict(f) for f in CLOCK_FACES],
        "color_presets": [dict(c) for c in COLOR_PRESETS],
        "gradients": [dict(g) for g in GRADIENTS],
        "live_lines": list(LIVE_LINES),
        "date_formats": [{"id": k, "label": v} for k, v in DATE_STYLES.items()],
        "screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
    }
