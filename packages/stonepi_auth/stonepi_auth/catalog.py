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
]

APP_IDS = tuple(item["id"] for item in APP_CATALOG)
LAUNCHER_APP_IDS = tuple(item["id"] for item in APP_CATALOG if item.get("launcher"))
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
