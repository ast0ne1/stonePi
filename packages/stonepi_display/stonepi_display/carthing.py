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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480
MAX_CONFIGS = 20
MAX_SNAPSHOTS = 5
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
    # F7: Spotify through the panel service itself (no /api/display feed).
    {"id": "music", "label": "Music", "source": "spotify"},
)
MINI_APP_IDS = tuple(a["id"] for a in MINI_APPS)

# What a page can hold: every mini-app (its card) plus the clock & weather and weather widgets.
WIDGETS: tuple[dict[str, str], ...] = (
    *MINI_APPS,
    {"id": "clock", "label": "Clock & weather", "source": ""},
    {"id": "weather", "label": "Weather", "source": ""},
    # The clock face and date alone (no weather), in the Clock tab's face and colours.
    {"id": "time", "label": "Clock", "source": ""},
)
WIDGET_IDS = tuple(w["id"] for w in WIDGETS)
# A web page (F14) on a page: "web:<page id>" from Notify's Web pages, always full page.
WEB_WIDGET_PREFIX = "web:"
_WEB_PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def web_widget_page(widget_id: Any) -> str:
    """The web page id in a ``web:<id>`` widget id, or ""."""
    text = str(widget_id or "")
    if not text.startswith(WEB_WIDGET_PREFIX):
        return ""
    page_id = text[len(WEB_WIDGET_PREFIX):]
    return page_id if _WEB_PAGE_ID.match(page_id) else ""

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
    # Straight to the dimmed screensaver (backlight off where the panel controls it).
    {"id": "sleep", "label": "Sleep", "group": "Screen"},
    {"id": "brightness_up", "label": "Brighter", "group": "Screen"},
    {"id": "brightness_down", "label": "Dimmer", "group": "Screen"},
    {"id": "restart", "label": "Restart a service", "group": "Admin"},
)
MUSIC_PRESETS = 4
_MUSIC_ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "music:play_pause", "label": "Play / pause", "group": "Music"},
    {"id": "music:next", "label": "Next track", "group": "Music"},
    {"id": "music:prev", "label": "Previous track", "group": "Music"},
    {"id": "music:vol_up", "label": "Volume up", "group": "Music"},
    {"id": "music:vol_down", "label": "Volume down", "group": "Music"},
    {"id": "music:shuffle", "label": "Shuffle on / off", "group": "Music"},
    {"id": "music:repeat", "label": "Repeat (off, all, one)", "group": "Music"},
    {"id": "music:save", "label": "Save to Liked Songs", "group": "Music"},
    *({"id": f"music:preset:{n}", "label": f"Play music preset {n}", "group": "Music"} for n in range(1, MUSIC_PRESETS + 1)),
)
MUSIC_ACTION_IDS = tuple(a["id"] for a in _MUSIC_ACTIONS)
ACTIONS: tuple[dict[str, str], ...] = (
    *_NAV_ACTIONS,
    *_PAGE_ACTIONS,
    *({"id": f"open:{a['id']}", "label": f"Open {a['label']}", "group": "Mini-apps"} for a in MINI_APPS),
    # The large weather screen (U14); also what tapping a Weather widget opens.
    {"id": "open:weather", "label": "Open Weather", "group": "Mini-apps"},
    *_MUSIC_ACTIONS,
    *_DEVICE_ACTIONS,
)
ACTION_IDS = tuple(a["id"] for a in ACTIONS)
# Actions added after configs started saving an allow-list. A config remembers the actions
# that existed when it was saved (``actions_known``); anything newer is allowed by default,
# so an older allow-list never silently forbids it. Configs saved before ``actions_known``
# existed knew every action except these.
ADDED_ACTIONS = frozenset({"sleep", "open:weather", "open:music", *MUSIC_ACTION_IDS})

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
# What tapping (or dial-pressing) a Clock & weather widget does (U14).
CLOCK_TAPS: tuple[dict[str, str], ...] = (
    {"id": "none", "label": "Nothing"},
    {"id": "weather", "label": "Open the weather screen"},
)
CLOCK_TAP_IDS = tuple(t["id"] for t in CLOCK_TAPS)

# What the idle screen draws. Clock = today's faces (with date, weather and the live line);
# the others are animations drawn by panel.js. A new style needs an entry here, its options
# in SAVER_DEFAULTS (normalised in _normalize_saver), a block in saver.html and a SAVERS entry
# in panel.js. ``group`` places it in the editor's Style dropdown (SAVER_GROUPS order).
SAVER_GROUPS: tuple[dict[str, str], ...] = (
    {"id": "info", "label": "Clock & info"},
    {"id": "classic", "label": "Classic"},
    {"id": "win98", "label": "Windows 98"},
)
SAVER_STYLES: tuple[dict[str, str], ...] = (
    {"id": "clock", "label": "Clock", "group": "info"},
    {"id": "web", "label": "Web page", "group": "info"},
    {"id": "logo", "label": "Bouncing logo", "group": "classic"},
    {"id": "matrix", "label": "Matrix", "group": "classic"},
    {"id": "pipes", "label": "3D Pipes", "group": "win98"},
    {"id": "starfield", "label": "Flying Through Space", "group": "win98"},
    {"id": "mystify", "label": "Mystify Your Mind", "group": "win98"},
    {"id": "flowerbox", "label": "3D Flower Box", "group": "win98"},
    {"id": "maze", "label": "3D Maze", "group": "win98"},
)
SAVER_STYLE_IDS = tuple(s["id"] for s in SAVER_STYLES)
SAVER_SPEEDS = ("slow", "normal", "fast")
SAVER_DENSITIES = ("light", "normal", "dense")
# Mystify Your Mind: how long the trail of each shape is.
SAVER_TRAILS = ("short", "normal", "long")
MYSTIFY_SHAPES = (1, 3)  # shapes on screen, min and max
# 3D Flower Box: a checkerboard on every face, or one colour per face, as in the original.
SAVER_FLOWER_COLORS: tuple[dict[str, str], ...] = (
    {"id": "checker", "label": "Checkerboard"},
    {"id": "faces", "label": "A colour per side"},
)
SAVER_FLOWER_COLOR_IDS = tuple(c["id"] for c in SAVER_FLOWER_COLORS)
# 3D Maze wall textures (drawn by panel.js, no image files).
SAVER_MAZE_WALLS: tuple[dict[str, str], ...] = (
    {"id": "brick", "label": "Brick"},
    {"id": "stone", "label": "Stone"},
    {"id": "wood", "label": "Wood"},
)
SAVER_MAZE_WALL_IDS = tuple(w["id"] for w in SAVER_MAZE_WALLS)
# Where a screensaver's time sits and how big it is (Show time on an animated style). Every
# size is the screensaver clock's face. Matrix keeps its own (a big face in the middle).
TIME_POSITIONS: tuple[dict[str, str], ...] = (
    {"id": "top-left", "label": "Top left"},
    {"id": "top-center", "label": "Top centre"},
    {"id": "top-right", "label": "Top right"},
    {"id": "center", "label": "Centre"},
    {"id": "bottom-left", "label": "Bottom left"},
    {"id": "bottom-center", "label": "Bottom centre"},
    {"id": "bottom-right", "label": "Bottom right"},
)
TIME_POSITION_IDS = tuple(p["id"] for p in TIME_POSITIONS)
TIME_SIZES: tuple[dict[str, str], ...] = (
    {"id": "small", "label": "Small"},
    {"id": "medium", "label": "Medium"},
    {"id": "large", "label": "Large"},
    {"id": "xl", "label": "Extra large"},
)
TIME_SIZE_IDS = tuple(s["id"] for s in TIME_SIZES)
SAVER_TIME_DEFAULTS: dict[str, Any] = {"time_pos": "bottom-right", "time_size": "small", "time_backing": False}

# The header strip on the pages (time, date, page title, page dots, weather). The config has a
# default; a page can override it with its own. "auto" shows it on arriving at a page or on
# input, then hides it after hide_after_s, with the page laid out full screen underneath.
HEADER_MODES: tuple[dict[str, str], ...] = (
    {"id": "always", "label": "Always"},
    {"id": "auto", "label": "Auto-hide"},
    {"id": "off", "label": "Off"},
)
HEADER_MODE_IDS = tuple(m["id"] for m in HEADER_MODES)
HEADER_HIDE_AFTER = (3, 5, 10)
HEADER_DEFAULTS: dict[str, Any] = {
    "mode": "always", "hide_after_s": 5, "time": True, "title": False, "weather": True, "dots": True,
}


def normalize_header(raw: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """A header (config default or a page's own), filled from ``base`` (else the defaults)."""
    data = raw if isinstance(raw, dict) else {}
    out = dict(base or HEADER_DEFAULTS)
    out["mode"] = _choice(data.get("mode"), HEADER_MODE_IDS, out["mode"])
    try:
        hide = int(data.get("hide_after_s", out["hide_after_s"]))
    except (TypeError, ValueError):
        hide = out["hide_after_s"]
    out["hide_after_s"] = hide if hide in HEADER_HIDE_AFTER else HEADER_DEFAULTS["hide_after_s"]
    for key in ("time", "title", "weather", "dots"):
        if key in data:
            out[key] = bool(data[key])
    return out


def page_header(config: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """The header a page shows: its own, else the config's default."""
    return page.get("header") or config.get("header") or dict(HEADER_DEFAULTS)


# 3D Pipes corners, as in the Windows original: rounded elbows, ball joints, or mostly elbows.
SAVER_JOINTS: tuple[dict[str, str], ...] = (
    {"id": "elbow", "label": "Elbows"},
    {"id": "ball", "label": "Ball joints"},
    {"id": "mixed", "label": "Mixed"},
)
SAVER_JOINT_IDS = tuple(j["id"] for j in SAVER_JOINTS)
MATRIX_GREEN = "#00FF41"
SAVER_DEFAULTS: dict[str, dict[str, Any]] = {
    "logo": {"recolor": True, "corner": True, "speed": "slow", "time": False},
    "matrix": {"color": MATRIX_GREEN, "time": True, "speed": "normal", "density": "normal",
               "time_pos": "center", "time_size": "xl"},
    "pipes": {"speed": "normal", "joints": "mixed", "time": False},
    # A snapshot of one of Notify's Web pages (F14), refreshed every refresh_min while it shows.
    "web": {"page_id": "", "refresh_min": 2, "time": False},
    "starfield": {"speed": "normal", "density": "normal", "color": False, "time": False},
    "mystify": {"speed": "normal", "shapes": 2, "trail": "normal", "time": False},
    "flowerbox": {"speed": "normal", "colors": "checker", "time": False},
    "maze": {"speed": "normal", "walls": "brick", "rat": True, "time": False},
}
WEB_SAVER_REFRESH = (1, 5)  # minutes

# Design-system defaults for new configs (F6); saved configs keep their own colours.
SAND = "#C7BBA8"  # brand-sand
STONE_TEXT = "#ECEAE6"  # text (dark theme)
BASALT = "#0B0D0E"  # canvas (dark theme)

COLOR_PRESETS: tuple[dict[str, str], ...] = (
    {"id": "sand", "label": "Sand", "value": SAND},
    {"id": "stone", "label": "Stone", "value": STONE_TEXT},
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
LIVE_LINES = ("none", "eventtrakr", "sportguide", "newscast", "pinboard", "music")
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
    # Swipes turn pages, like a book (left = forward); any control can be remapped.
    "swipe_left": "next_page",
    "swipe_right": "prev_page",
    "swipe_up": "sleep",
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
        "header": dict(HEADER_DEFAULTS),
        "allowed_actions": list(ACTION_IDS),
        "actions_known": list(ACTION_IDS),
        "controls": dict(DEFAULT_CONTROLS),
        "idle": {
            "screensaver_after_s": 60,
            "dim_after_s": 300,
            "dim_level": 25,
            "quiet_hours": {"enabled": True, "from": "22:30", "to": "06:30", "mode": "dim", "level": 5},
            "background": {"type": "solid", "value": BASALT},
            "show": {"date": True, "weather": True, "live_line": "eventtrakr"},
            "saver": {"style": "clock", **SAVER_TIME_DEFAULTS, **{k: dict(v) for k, v in SAVER_DEFAULTS.items()}},
        },
        "clock": {
            "face": "digital_bold",
            "color": STONE_TEXT,
            "accent": SAND,
            "quiet_color": None,
            "hour_format": "24",
            "seconds": False,
            "date_format": "ddd d MMM",
            "tap": "none",
            # U13: the screensaver's own face and colours, used only when ``separate`` is on.
            "saver": {"separate": False, "face": "digital_bold", "color": STONE_TEXT, "accent": SAND},
        },
        "look": {"theme": "dark", "accent": SAND, "units": "metric"},
        "weather": {"lat": None, "lon": None, "label": ""},
        "music": default_music(),
    }


MUSIC_SAVER_TIME_DEFAULTS: dict[str, Any] = {"show": True, "pos": "top-right", "size": "small", "backing": False}


def _normalize_music_saver_time(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = dict(MUSIC_SAVER_TIME_DEFAULTS)
    for key in ("show", "backing"):
        if key in data:
            out[key] = bool(data[key])
    out["pos"] = _choice(data.get("pos"), TIME_POSITION_IDS, out["pos"])
    out["size"] = _choice(data.get("size"), TIME_SIZE_IDS, out["size"])
    return out


def default_music() -> dict[str, Any]:
    """F7 Music: presets 1-4, the speaker to use when nothing is playing, and options."""
    return {
        "presets": [{"name": "", "uri": "", "device": {"id": "", "name": ""}} for _ in range(MUSIC_PRESETS)],
        "default_device": {"id": "", "name": ""},
        # The dial turns the volume while Now Playing is on screen (falls back to its mapping
        # when the speaker refuses remote volume or volume actions aren't allowed).
        "dial_volume": True,
        "volume_step": 4,
        # The screensaver shows the artwork while music plays, with the time in a corner.
        "saver_artwork": True,
        # That screensaver's time: its own show / position / size / backing (the screensaver clock face).
        "saver_time": dict(MUSIC_SAVER_TIME_DEFAULTS),
        "show_save": True,
        # Now Playing's look: background, when the control row shows, where the accent comes
        # from, and an optional thin line round the screen in the accent.
        "np": dict(NP_DEFAULTS),
    }


# Now Playing (music screen, full-page music widget, artwork screensaver).
NP_BACKGROUNDS: tuple[dict[str, str], ...] = (
    {"id": "glow", "label": "Glow behind the artwork"},
    {"id": "art", "label": "Artwork colour (gradient)"},
    {"id": "blur", "label": "Blurred artwork"},
    {"id": "theme", "label": "Theme (plain)"},
)
NP_CONTROLS: tuple[dict[str, str], ...] = (
    {"id": "always", "label": "Always"},
    {"id": "touch", "label": "When touched (4 s)"},
    {"id": "never", "label": "Never (buttons and dial only)"},
)
NP_ACCENTS: tuple[dict[str, str], ...] = (
    {"id": "art", "label": "From the artwork"},
    {"id": "theme", "label": "The panel's accent"},
)
NP_DEFAULTS: dict[str, Any] = {"background": "glow", "controls": "always", "accent": "art", "edge": False}


def _normalize_np(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {
        "background": _choice(data.get("background"), tuple(b["id"] for b in NP_BACKGROUNDS), NP_DEFAULTS["background"]),
        "controls": _choice(data.get("controls"), tuple(c["id"] for c in NP_CONTROLS), NP_DEFAULTS["controls"]),
        "accent": _choice(data.get("accent"), tuple(a["id"] for a in NP_ACCENTS), NP_DEFAULTS["accent"]),
        "edge": bool(data.get("edge", NP_DEFAULTS["edge"])),
    }


_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _music_device(raw: Any) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    device_id = str(data.get("id") or "").strip()
    return {
        "id": device_id if _DEVICE_ID.match(device_id) else "",
        "name": str(data.get("name") or "").strip()[:60],
    }


def _normalize_music(raw: Any) -> dict[str, Any]:
    from .spotify import normalize_uri

    data = raw if isinstance(raw, dict) else {}
    out = default_music()
    presets_raw = data.get("presets") if isinstance(data.get("presets"), list) else []
    for i, entry in enumerate(presets_raw[:MUSIC_PRESETS]):
        entry = entry if isinstance(entry, dict) else {}
        out["presets"][i] = {
            "name": str(entry.get("name") or "").strip()[:40],
            "uri": normalize_uri(entry.get("uri")),
            "device": _music_device(entry.get("device")),
        }
    out["default_device"] = _music_device(data.get("default_device"))
    for key in ("dial_volume", "saver_artwork", "show_save"):
        if key in data:
            out[key] = bool(data.get(key))
    out["volume_step"] = _int(data.get("volume_step"), out["volume_step"], 1, 10)
    out["np"] = _normalize_np(data.get("np"))
    out["saver_time"] = _normalize_music_saver_time(data.get("saver_time"))
    return out


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


def _normalize_saver(raw: Any, saver: dict[str, Any]) -> None:
    """Fill ``saver`` (the defaults) from ``raw``; unknown or missing values keep the defaults."""
    raw = raw if isinstance(raw, dict) else {}
    saver["style"] = _choice(raw.get("style"), SAVER_STYLE_IDS, saver["style"])
    saver["time_pos"] = _choice(raw.get("time_pos"), TIME_POSITION_IDS, saver["time_pos"])
    saver["time_size"] = _choice(raw.get("time_size"), TIME_SIZE_IDS, saver["time_size"])
    if "time_backing" in raw:
        saver["time_backing"] = bool(raw["time_backing"])
    for style, defaults in SAVER_DEFAULTS.items():
        opts_raw = raw.get(style) if isinstance(raw.get(style), dict) else {}
        opts = saver[style]
        for key, default in defaults.items():
            if key not in opts_raw:
                continue
            value = opts_raw[key]
            if isinstance(default, bool):
                opts[key] = bool(value)
            elif key == "color":
                opts[key] = _color(value, default)
            elif key == "speed":
                opts[key] = _choice(value, SAVER_SPEEDS, default)
            elif key == "density":
                opts[key] = _choice(value, SAVER_DENSITIES, default)
            elif key == "joints":
                opts[key] = _choice(value, SAVER_JOINT_IDS, default)
            elif key == "time_pos":
                opts[key] = _choice(value, TIME_POSITION_IDS, default)
            elif key == "time_size":
                opts[key] = _choice(value, TIME_SIZE_IDS, default)
            elif key == "trail":
                opts[key] = _choice(value, SAVER_TRAILS, default)
            elif key == "colors":
                opts[key] = _choice(value, SAVER_FLOWER_COLOR_IDS, default)
            elif key == "walls":
                opts[key] = _choice(value, SAVER_MAZE_WALL_IDS, default)
            elif key == "shapes":
                try:
                    opts[key] = min(MYSTIFY_SHAPES[1], max(MYSTIFY_SHAPES[0], int(value)))
                except (TypeError, ValueError):
                    opts[key] = default
            elif key == "page_id":
                page_id = str(value or "").strip()
                opts[key] = page_id if _WEB_PAGE_ID.match(page_id) else default
            elif key == "refresh_min":
                try:
                    opts[key] = min(WEB_SAVER_REFRESH[1], max(WEB_SAVER_REFRESH[0], int(value)))
                except (TypeError, ValueError):
                    opts[key] = default


def clean_config_id(value: Any) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")[:40]
    return text if _ID.match(text or "") else ""


def _normalize_widget(item: Any) -> dict[str, str] | None:
    """``"newscast"`` (schema 1) or ``{"id": "newscast", "size": "half"}``."""
    if isinstance(item, dict):
        wid, size = str(item.get("id") or "").strip(), str(item.get("size") or "").strip()
    else:
        wid, size = str(item or "").strip(), "small"
    if web_widget_page(wid):
        return {"id": wid, "size": "full"}
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
        page: dict[str, Any] = {"name": name, "widgets": widgets}
        if isinstance(entry.get("header"), dict):
            page["header"] = normalize_header(entry["header"])  # its own; else the config's default
        pages.append(page)
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
    out["header"] = normalize_header(data.get("header"))

    allowed_raw = data.get("allowed_actions")
    if isinstance(allowed_raw, list):
        allowed_set = {str(a) for a in allowed_raw}
        # Back and Home always work: without them the panel can strand you.
        allowed_set |= {"home", "back"}
        if legacy:
            # Page actions didn't exist yet: an old allow-list mustn't silently forbid them.
            allowed_set |= set(PAGE_ACTION_IDS)
        known_raw = data.get("actions_known")
        known = {str(a) for a in known_raw} if isinstance(known_raw, list) else set(ACTION_IDS) - ADDED_ACTIONS
        allowed_set |= set(ACTION_IDS) - known  # actions newer than this allow-list
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
        bg_value = _color(bg_value, BASALT) or BASALT
    elif bg_type == "image":
        if not _ASSET.match(bg_value):
            bg_type, bg_value = "solid", BASALT
    else:
        bg_value = bg_value if bg_value in GRADIENT_IDS else "midnight"
    idle["background"] = {"type": bg_type, "value": bg_value}
    show_raw = idle_raw.get("show") if isinstance(idle_raw.get("show"), dict) else {}
    show = idle["show"]
    for key in ("date", "weather"):
        if key in show_raw:
            show[key] = bool(show_raw.get(key))
    show["live_line"] = _choice(show_raw.get("live_line"), LIVE_LINES, show["live_line"])
    _normalize_saver(idle_raw.get("saver"), idle["saver"])

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
    clock["tap"] = _choice(clock_raw.get("tap"), CLOCK_TAP_IDS, clock["tap"])
    # Separate screensaver clock: missing values follow the widget clock, so switching it on
    # starts from what's on screen today. Off (the default) = one face everywhere.
    saver_raw = clock_raw.get("saver") if isinstance(clock_raw.get("saver"), dict) else {}
    clock["saver"] = {
        "separate": bool(saver_raw.get("separate")),
        "face": _choice(saver_raw.get("face"), CLOCK_FACE_IDS, clock["face"]),
        "color": _color(saver_raw.get("color"), clock["color"]),
        "accent": _color(saver_raw.get("accent"), clock["accent"]),
    }

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
    out["music"] = _normalize_music(data.get("music"))
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


def music_account() -> dict[str, Any]:
    """The Spotify account linked for the Music mini-app (name only; tokens live in the vault)."""
    data = _read_json(_root() / "music.json")
    data = data if isinstance(data, dict) else {}
    alerts = data.get("alerts") if isinstance(data.get("alerts"), dict) else {}
    return {
        "name": str(data.get("name") or "")[:80],
        "id": str(data.get("id") or "")[:80],
        "connected_at": data.get("connected_at") or None,
        "expires_on": _parse_day(data.get("expires_on")),  # set by the admin; None = 6 months on
        "alerts": {str(k): str(v) for k, v in alerts.items()},
    }


def save_music_account(name: str, account_id: str) -> dict[str, Any]:
    # A fresh sign-in starts a fresh 6 months, so a set expiry and sent alerts are dropped.
    record = {"name": str(name or "")[:80], "id": str(account_id or "")[:80], "connected_at": _now()}
    with _lock:
        _write_json(_root() / "music.json", record)
    return record


# Spotify refresh tokens expire a fixed 6 months after sign-in (U21); Spotify doesn't say
# when, so the date is worked out from connected_at, and the admin can correct it.
MUSIC_SIGNIN_MONTHS = 6
MUSIC_WARN_DAYS = 14


def _parse_day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise ValueError(day)


def _update_music(**fields: Any) -> None:
    with _lock:
        data = _read_json(_root() / "music.json")
        if not isinstance(data, dict) or not data.get("connected_at"):
            raise ValueError("Spotify isn't connected.")
        data.update(fields)
        _write_json(_root() / "music.json", data)


def set_music_expiry(value: Any) -> str | None:
    """Set the sign-in expiry (YYYY-MM-DD), or clear it with "" to use 6 months from connecting.

    Sent alerts are dropped too, so a new date warns again."""
    day = _parse_day(value) if value else None
    if value and day is None:
        raise ValueError("That isn't a date.")
    _update_music(expires_on=day, alerts={})
    return day


def mark_music_alert(kind: str, expires_on: str) -> None:
    """Remember an expiry alert was sent for this date, so the daily check sends it once."""
    alerts = dict(music_account()["alerts"])
    alerts[kind] = expires_on
    _update_music(alerts=alerts)


def music_expiry(account: dict[str, Any] | None = None, *, error_code: str = "", today: date | None = None) -> dict[str, Any] | None:
    """When the Spotify sign-in runs out, or None when not connected.

    ``state``: ok, soon (last MUSIC_WARN_DAYS days), expired (date passed) or revoked
    (the Car Thing service got invalid_grant, whatever the date says)."""
    account = account if account is not None else music_account()
    connected = _parse_day(account.get("connected_at"))
    if not connected:
        return None
    estimated = _add_months(date.fromisoformat(connected), MUSIC_SIGNIN_MONTHS).isoformat()
    expires_on = account.get("expires_on") or estimated
    days_left = (date.fromisoformat(expires_on) - (today or datetime.now(timezone.utc).date())).days
    if error_code == "invalid_grant":
        state = "revoked"
    elif days_left <= 0:
        state = "expired"
    elif days_left <= MUSIC_WARN_DAYS:
        state = "soon"
    else:
        state = "ok"
    return {
        "expires_on": expires_on,
        "estimated_on": estimated,
        "set_by_admin": bool(account.get("expires_on")),
        "days_left": days_left,
        "state": state,
    }


def clear_music_account() -> None:
    with _lock:
        try:
            (_root() / "music.json").unlink()
        except FileNotFoundError:
            pass


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
        "clock_taps": [dict(t) for t in CLOCK_TAPS],
        "saver_styles": [dict(s) for s in SAVER_STYLES],
        "saver_groups": [dict(g) for g in SAVER_GROUPS],
        "time_positions": [dict(p) for p in TIME_POSITIONS],
        "time_sizes": [dict(s) for s in TIME_SIZES],
        "header_modes": [dict(m) for m in HEADER_MODES],
        "header_hide_after": list(HEADER_HIDE_AFTER),
        "header_defaults": dict(HEADER_DEFAULTS),
        "np_backgrounds": [dict(b) for b in NP_BACKGROUNDS],
        "np_controls": [dict(c) for c in NP_CONTROLS],
        "np_accents": [dict(a) for a in NP_ACCENTS],
        "saver_trails": list(SAVER_TRAILS),
        "saver_flower_colors": [dict(c) for c in SAVER_FLOWER_COLORS],
        "saver_maze_walls": [dict(w) for w in SAVER_MAZE_WALLS],
        "saver_joints": [dict(j) for j in SAVER_JOINTS],
        "saver_speeds": list(SAVER_SPEEDS),
        "saver_densities": list(SAVER_DENSITIES),
        "color_presets": [dict(c) for c in COLOR_PRESETS],
        # StonePi's own colours, for the editor's "Default" buttons next to each colour.
        "color_defaults": {
            "look.accent": SAND,
            "clock.color": STONE_TEXT,
            "clock.accent": SAND,
            "clock.saver.color": STONE_TEXT,
            "clock.saver.accent": SAND,
        },
        "gradients": [dict(g) for g in GRADIENTS],
        "live_lines": list(LIVE_LINES),
        "music_presets": MUSIC_PRESETS,
        "date_formats": [{"id": k, "label": v} for k, v in DATE_STYLES.items()],
        "screen": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
    }
