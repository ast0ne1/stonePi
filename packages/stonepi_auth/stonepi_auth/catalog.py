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
        "description": "Shared household sign-in.",
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
            {"id": "can_use_ntfy", "label": "Phone alerts"},
            {"id": "can_view_status", "label": "View status"},
        ],
        "launcher": True,
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
        "capabilities": [],
        "launcher": True,
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
        "capabilities": [],
        "launcher": True,
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
        "capabilities": [],
        "launcher": True,
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
            {"id": "can_use_llm", "label": "Use LLM"},
            {"id": "can_publish", "label": "Publish to FileServe"},
        ],
        "launcher": True,
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
            {"id": "can_use_alerts", "label": "Offer alerts"},
        ],
        "launcher": True,
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
        "icon": "sportguide",
        "description": "What’s on sports TV and streams — AFL, Cricket, Rugby, Football.",
    },
]

APP_IDS = tuple(item["id"] for item in APP_CATALOG)
LAUNCHER_APP_IDS = tuple(item["id"] for item in APP_CATALOG if item.get("launcher"))


def app_by_id(app_id: str) -> dict[str, Any] | None:
    for item in APP_CATALOG:
        if item["id"] == app_id:
            return item
    return None


def capabilities_for(app_id: str) -> list[dict[str, str]]:
    item = app_by_id(app_id)
    if not item:
        return []
    return list(item.get("capabilities") or [])
