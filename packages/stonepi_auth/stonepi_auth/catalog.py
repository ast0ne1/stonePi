from __future__ import annotations

from typing import Any

# Capability definitions are the generic contract apps advertise to the portal.
# Dashboard People UI renders these; auth stores selected flags on each grant;
# apps may sync them from the platform session on login.

APP_CATALOG: list[dict[str, Any]] = [
    {
        "id": "dashboard",
        "name": "Dashboard",
        "path": "/",
        "port": 8010,
        "unit": "stonepi-dashboard",
        "health": "/healthz",
        "color": "#b08900",
        "capabilities": [],
        "launcher": False,
        "group": "system",
        "ships_with": "platform",
        "description": "StonePi home and administration.",
    },
    {
        "id": "auth",
        "name": "Auth",
        "path": "/auth/",
        "port": 8011,
        "unit": "stonepi-auth",
        "health": "/healthz",
        "color": "#5a4632",
        "capabilities": [],
        "launcher": False,
        "group": "system",
        "ships_with": "platform",
        "description": "Shared household sign-in.",
    },
    {
        "id": "notify",
        "name": "Notify",
        "path": "/notify/",
        "port": 8012,
        "unit": "stonepi-notify",
        "health": "/healthz",
        "color": "#4a5568",
        "capabilities": [],
        "launcher": False,
        "group": "system",
        "ships_with": "platform",
        "icon": "notify",
        # Admin-only; people manage their alerts on the Dashboard Notifications page.
        "grants": False,
        "description": "Displays, TRMNL, and ntfy destinations for the household.",
    },
    {
        # Agents and agent devices (Pebble first) reach StonePi only through here: bindings,
        # tool policy and audit (ideation/muse.md, ideation/pebble.md). No UI of its own;
        # admins manage devices in Notify → Sidekick, so that's where its link goes.
        "id": "agentlink",
        "name": "AgentLink",
        "path": "/notify/sidekick",
        "port": 8015,
        "unit": "stonepi-agentlink",
        "health": "/healthz",
        "color": "#4a5568",
        "capabilities": [],
        "launcher": False,
        "group": "system",
        "ships_with": "platform",
        "icon": "agentlink",
        "grants": False,
        "description": "Pebble and other agent devices: pairing, what they may use, and the activity log.",
    },
    {
        "id": "recover",
        "name": "Recover",
        "path": "/recover/",
        "port": 8099,
        "unit": "stonepi-recover",
        "health": "/healthz",
        "color": "#7f1d1d",
        "capabilities": [],
        "launcher": False,
        "grants": False,
        "group": "system",
        "icon": "recover",
        "ships_with": "platform",
        "description": "Escape hatch when Dashboard or nginx is unhealthy.",
    },
    {
        "id": "newscast",
        "name": "NewsCast",
        "path": "/news/",
        "port": 8001,
        "unit": "stonepi-newscast",
        "health": "/healthz",
        "color": "#8b1e1e",
        "capabilities": [
            {"id": "can_add_custom_sources", "label": "Add custom feeds"},
            {"id": "can_view_status", "label": "View status"},
        ],
        "launcher": True,
        "group": "user",
        "icon": "newscast",
        "description": "Daily briefings from the feeds you choose, ready for your e-reader.",
    },
    {
        "id": "fileserve",
        "name": "FileServe",
        "path": "/files/",
        "port": 8002,
        "unit": "stonepi-fileserve",
        "health": "/healthz",
        "color": "#1d5a8a",
        "capabilities": [
            {"id": "can_publish_pages", "label": "Publish pages", "default": True},
            {"id": "can_publish_unprotected", "label": "Publish without a password", "default": False},
        ],
        "launcher": True,
        "group": "user",
        "icon": "fileserve",
        "description": "Host and share pages and files on your home network.",
    },
    {
        "id": "eventtrakr",
        "name": "EventTrakr",
        "path": "/events/",
        "port": 8003,
        "unit": "stonepi-eventtrakr",
        "health": "/healthz",
        "color": "#3a5628",
        # "default": what a member gets until an admin saves their toggles (Auth fills
        # it in for grants that predate the capability, and for new people).
        "capabilities": [
            {"id": "can_manage_sources", "label": "Manage sources", "default": True},
            {"id": "can_use_social", "label": "Instagram & Facebook", "default": False},
            {"id": "can_share_agenda", "label": "Share agenda publicly", "default": True},
            {"id": "can_sync_calendar", "label": "Google Calendar sync", "default": True},
        ],
        "launcher": True,
        "group": "user",
        "icon": "eventtrakr",
        "description": "Track local events, favourites, and calendar sync in one place.",
    },
    {
        "id": "pinboard",
        "name": "Pinboard",
        "path": "/pinboard/",
        "port": 8004,
        "unit": "stonepi-pinboard",
        "health": "/healthz",
        "color": "#6b4c2a",
        "capabilities": [
            {"id": "can_post_notices", "label": "Post notices", "default": True},
            {"id": "can_assign_others", "label": "Assign reminders to others", "default": True},
        ],
        "launcher": True,
        "group": "user",
        "icon": "pinboard",
        "description": "Household notices and short reminders.",
    },
    {
        "id": "studio",
        "name": "Studio",
        "path": "/studio/",
        "port": 8005,
        "unit": "stonepi-studio",
        "health": "/healthz",
        "color": "#5c3d6e",
        "capabilities": [
            {"id": "can_use_llm", "label": "Use LLM", "default": False},
            {"id": "can_publish", "label": "Publish to FileServe", "default": False},
        ],
        "launcher": True,
        "group": "user",
        "icon": "studio",
        "description": "Chat-build static sites and publish them to FileServe.",
    },
    {
        "id": "pricescout",
        "name": "PriceScout",
        "path": "/prices/",
        "port": 8006,
        "unit": "stonepi-pricescout",
        "health": "/healthz",
        "color": "#2f6f4e",
        "capabilities": [
            {"id": "can_manage_sources", "label": "Manage sources"},
        ],
        "launcher": True,
        "group": "user",
        "icon": "pricescout",
        "description": "Weekly supermarket offers from eTilbudsavis, compared on your LAN.",
    },
    {
        "id": "sportguide",
        "name": "SportGuide",
        "path": "/sports/",
        "port": 8007,
        "unit": "stonepi-sportguide",
        "health": "/healthz",
        "color": "#1d5a8a",
        "capabilities": [
            {"id": "can_refresh", "label": "Refresh schedules"},
        ],
        "launcher": True,
        "group": "user",
        "icon": "sportguide",
        "description": "What’s on sports TV and streams — AFL, Cricket, Rugby, Football.",
    },
    {
        "id": "pricewatch",
        "name": "PriceWatch",
        "path": "/watch/",
        "port": 8008,
        "unit": "stonepi-pricewatch",
        "health": "/healthz",
        "color": "#b45309",
        "capabilities": [
            {"id": "can_manage_watches", "label": "Manage watches", "default": True},
            {"id": "can_manage_sources", "label": "Manage sources"},
            {"id": "can_use_alerts", "label": "Strike alerts", "default": True},
        ],
        "launcher": True,
        "group": "user",
        "icon": "pricewatch",
        "description": "Watch specific products and get notified when your target price is met.",
    },
    {
        "id": "library",
        "name": "Library",
        "path": "/library/",
        "port": 8009,
        "unit": "stonepi-library",
        "health": "/healthz",
        "color": "#3f5f7a",
        "capabilities": [
            {"id": "can_manage_content", "label": "Manage content"},
        ],
        "launcher": True,
        "group": "user",
        "icon": "library",
        # While a download runs, the Home tile shows its progress ("Installing Wikipedia · 71%").
        "live_status": True,
        "description": "Wikipedia and other references, offline on your Pi.",
    },
    {
        # Family updates for a TRMNL at the family's home (ideation/ripple.md). Anyone granted
        # Ripple reads and posts; authors change their own posts, admins any post.
        "id": "ripple",
        "name": "Ripple",
        "path": "/ripple/",
        "port": 8017,
        "unit": "stonepi-ripple",
        "health": "/healthz",
        "color": "#1b3550",
        "capabilities": [],
        "launcher": True,
        "group": "user",
        "icon": "ripple",
        # Its family display page (/display) opens for Notify's capture only with a one-off key
        # from this signed endpoint (Notify adds it when a web page points at this app's port).
        "capture_key": "/api/internal/capture-key",
        # Offered as its own kind in Notify → Displays → Add a Display (name, model, webhook).
        "display_page": "/display",
        "display_label": "Ripple family board",
        "display_blurb": "Family posts and photos from Ripple, prepared for e-ink on the Pi.",
        "description": "Keep family far away up to date with news and photos.",
    },
]

APP_IDS = tuple(item["id"] for item in APP_CATALOG)
# Home tile order for anyone who hasn't reordered their own (catalog order is for ports,
# zips and the installer, so it stays as it is). Launcher apps not listed follow, in catalog order.
DEFAULT_LAUNCHER_ORDER = (
    "newscast",
    "eventtrakr",
    "sportguide",
    "pricescout",
    "pricewatch",
    "pinboard",
    "ripple",
    "library",
    "fileserve",
    "studio",
)
LAUNCHER_APP_IDS = tuple(
    sorted(
        (item["id"] for item in APP_CATALOG if item.get("launcher")),
        key=lambda app_id: DEFAULT_LAUNCHER_ORDER.index(app_id) if app_id in DEFAULT_LAUNCHER_ORDER else len(DEFAULT_LAUNCHER_ORDER),
    )
)
SYSTEM_APP_IDS = tuple(item["id"] for item in APP_CATALOG if item.get("group") == "system")
# Apps with their own release zip / Dashboard update. System apps carry `ships_with: platform`:
# they take the platform version and ship inside the platform zip.
UPDATABLE_APP_IDS = tuple(item["id"] for item in APP_CATALOG if not item.get("ships_with"))
# Optional platform services under apps/ that are not apps (no tile, no grants, no Watch row):
# they ship in the platform zip and are switched on elsewhere (Car Thing: Notify → Displays).
PLATFORM_SERVICE_APP_IDS: tuple[str, ...] = ("carthing",)

# Pre-rename app ids still found in session cookies, grants and saved prefs.
LEGACY_APP_IDS: dict[str, str] = {"notifications": "notify", "recovery": "recover"}
SERVICE_GROUP_LABELS: tuple[tuple[str, str], ...] = (
    ("system", "SYSTEM"),
    ("user", "USER"),
)


def canonical_app_id(app_id: str) -> str:
    return LEGACY_APP_IDS.get(app_id, app_id)


def app_by_id(app_id: str) -> dict[str, Any] | None:
    for item in APP_CATALOG:
        if item["id"] == app_id:
            return item
    return None


def capabilities_for(app_id: str) -> list[dict[str, Any]]:
    item = app_by_id(app_id)
    if not item:
        return []
    return list(item.get("capabilities") or [])
