"""Per-person FileServe permissions (StonePi capabilities).

Admins have everything. Under StonePi, members get what Dashboard → Users grants
them; Auth fills in catalog defaults and puts the resolved map in the session
cookie, which :func:`app.auth.current_user` copies onto the request's user. A
capability the cookie doesn't carry (older cookie) falls back to the catalog
default. Solo FileServe (no platform secret) has no per-person switches, so local
accounts keep publishing as before.
"""

from __future__ import annotations

APP_ID = "fileserve"
PUBLISH_PAGES = "can_publish_pages"
PUBLISH_UNPROTECTED = "can_publish_unprotected"

# Studio's own switch for its Publish-to-FileServe button.
STUDIO_APP_ID = "studio"
STUDIO_PUBLISH = "can_publish"

# Used when the installed stonepi_auth predates FileServe's capabilities.
_FALLBACK = {
    PUBLISH_PAGES: ("Publish pages", True),
    PUBLISH_UNPROTECTED: ("Publish without a password", False),
}

ASK_PUBLISH = "Ask an admin to allow 'Publish pages' in StonePi → Users."
ASK_UNPROTECTED = (
    "Set a page username and password. Ask an admin to allow "
    "'Publish without a password' in StonePi → Users to publish openly."
)
ASK_STUDIO = "Ask an admin to allow 'Publish to FileServe' for Studio in StonePi → Users."


def _catalog() -> dict[str, tuple[str, bool]]:
    try:
        from stonepi_auth.catalog import capabilities_for

        items = capabilities_for(APP_ID)
    except Exception:  # noqa: BLE001
        items = []
    out = {
        str(item["id"]): (str(item.get("label") or item["id"]), bool(item.get("default", False)))
        for item in items
        if item.get("id") in _FALLBACK
    }
    return {**_FALLBACK, **out}


def defaults() -> dict[str, bool]:
    return {key: default for key, (_label, default) in _catalog().items()}


def resolve(granted: dict | None, cap: str) -> bool:
    """A member's capability: what the cookie says, else the catalog default."""
    if granted and cap in granted:
        return bool(granted[cap])
    return bool(defaults().get(cap, False))
