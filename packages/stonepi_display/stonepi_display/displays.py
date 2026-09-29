"""Multi-Display store for StonePi Notify."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from stonepi_contracts import WIDGET_CATALOG, widget_by_id

from .grid import allowed_sizes, place_widgets
from .templates import DATE_FORMATS, DEFAULT_DATE_FORMAT, TITLE_BAR_POSITIONS

_data_dir: Path | None = None
_lock = threading.Lock()
# Guards read-modify-write of displays.json (UI saves vs scheduler status updates).
_rmw_lock = threading.RLock()

BUILTIN_DASHBOARD_ID = "dashboard"

# Block ids from the pre-Display TRMNL editor (dashboard display.json) → widget ids.
# Only read when migrating an old install.
_LEGACY_BLOCKS: dict[str, str] = {w.legacy_block: w.id for w in WIDGET_CATALOG if w.legacy_block}


def configure_displays(data_dir: Path) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def _path() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_display displays not configured")
    return _data_dir / "displays.json"


def _legacy_display_path() -> Path | None:
    """Possible dashboard display.json locations for migration.

    Skip paths we cannot stat (e.g. Notify user cannot read Dashboard
    data/) — PermissionError must not crash boot.
    """
    if _data_dir is None:
        return None
    candidates = [
        _data_dir / "display.json",
        _data_dir.parent / "dashboard" / "data" / "display.json",
        _data_dir.parent / "dashboard" / "display.json",
        Path("/var/lib/stonepi/dashboard/data/display.json"),
        Path("/var/lib/stonepi/dashboard/display.json"),
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _default_widgets_from_blocks(layout: dict | None) -> list[dict[str, Any]]:
    items = []
    if isinstance(layout, dict):
        raw_items = layout.get("items") or []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                block_id = str(item.get("id") or "").strip()
                widget_id = _LEGACY_BLOCKS.get(block_id, "")
                if not widget_id:
                    continue
                span = item.get("span") or 1
                size = "large" if int(span) >= 2 else "medium"
                items.append(
                    {
                        "id": str(uuid.uuid4())[:8],
                        "widget_id": widget_id,
                        "size": size,
                        "config": {},
                    }
                )
    if not items:
        # Builtin overview: system + watch + apps + storage
        for widget_id in (
            "stonepi.system_status",
            "stonepi.watch",
            "stonepi.apps",
            "stonepi.storage",
        ):
            items.append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "widget_id": widget_id,
                    "size": "medium",
                    "config": {},
                }
            )
    return items


def _builtin_dashboard(widgets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": BUILTIN_DASHBOARD_ID,
        "name": "Dashboard",
        "enabled": True,
        "builtin": True,
        "device": "og",
        "title_bar": "bottom",
        "date_format": DEFAULT_DATE_FORMAT,
        "widgets": widgets if widgets is not None else _default_widgets_from_blocks(None),
        "trmnl": dict(TRMNL_DEFAULTS),
    }


TRMNL_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 30,
    # "universal": the plugin holds the StonePi universal template (layout
    # travels in the payload). "legacy": the plugin still holds markup pasted
    # from the old Destinations screen, so every flat variable is sent.
    "template": "universal",
    "last_push_at": None,
    "last_push_ok": None,
    "last_push_message": None,
    "last_push_status": None,
}
DISPLAY_DEVICES = ("og", "v2")
MIN_INTERVAL_MIN = 10
MAX_INTERVAL_MIN = 120


def _normalize_widget(raw: dict[str, Any]) -> dict[str, Any] | None:
    widget_id = str(raw.get("widget_id") or "").strip()
    if not widget_id and raw.get("id") and widget_by_id(str(raw["id"])):
        widget_id = str(raw["id"])
    if not widget_id:
        # legacy block id on instance
        legacy = str(raw.get("id") or "").strip()
        widget_id = _LEGACY_BLOCKS.get(legacy, "")
    if not widget_id or not widget_by_id(widget_id):
        return None
    sizes = allowed_sizes(widget_id)
    size = str(raw.get("size") or "medium").strip().lower()
    if size not in sizes:
        size = "medium" if "medium" in sizes else sizes[0]
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    try:
        x, y = max(0, int(raw.get("x") or 0)), max(0, int(raw.get("y") or 0))
    except (TypeError, ValueError):
        x = y = 0
    return {
        "id": str(raw.get("instance_id") or raw.get("id") or uuid.uuid4())[:36],
        "widget_id": widget_id,
        "size": size,
        "x": x,
        "y": y,
        "config": config,
    }


def _normalize_trmnl(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    out = dict(TRMNL_DEFAULTS)
    for key in out:
        if key in data:
            out[key] = data[key]
    out["enabled"] = bool(out["enabled"])
    try:
        out["interval_minutes"] = max(MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, int(out["interval_minutes"])))
    except (TypeError, ValueError):
        out["interval_minutes"] = TRMNL_DEFAULTS["interval_minutes"]
    out["template"] = "legacy" if out.get("template") == "legacy" else "universal"
    return out


def _normalize_display(raw: dict[str, Any]) -> dict[str, Any] | None:
    display_id = str(raw.get("id") or "").strip()
    if not display_id:
        return None
    name = str(raw.get("name") or display_id).strip() or display_id
    device = str(raw.get("device") or "og").strip().lower()
    if device not in DISPLAY_DEVICES:
        device = "og"
    title_bar = str(raw.get("title_bar") or "bottom").strip().lower()
    date_format = str(raw.get("date_format") or DEFAULT_DATE_FORMAT).strip().lower()
    widgets_raw = raw.get("widgets") if isinstance(raw.get("widgets"), list) else []
    widgets = []
    for item in widgets_raw:
        if isinstance(item, dict):
            normalized = _normalize_widget(item)
            if normalized:
                widgets.append(normalized)
    return {
        "id": display_id,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "builtin": bool(raw.get("builtin")) or display_id == BUILTIN_DASHBOARD_ID,
        "device": device,
        "title_bar": title_bar if title_bar in TITLE_BAR_POSITIONS else "bottom",
        "date_format": date_format if date_format in DATE_FORMATS else DEFAULT_DATE_FORMAT,
        "widgets": place_widgets(widgets, device),
        "trmnl": _normalize_trmnl(raw.get("trmnl")),
    }


normalize_display = _normalize_display


def migrate_from_legacy_display_json() -> dict[str, Any] | None:
    """Import dashboard display.json layout into destinations + displays once."""
    legacy = _legacy_display_path()
    if not legacy:
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    widgets = _default_widgets_from_blocks(data.get("layout") if isinstance(data.get("layout"), dict) else None)
    return {
        "trmnl": {
            "enabled": bool(data.get("enabled")),
            "webhook_url": str(data.get("webhook_url") or "").strip(),
            "interval_minutes": data.get("interval_minutes") or 30,
            "device": data.get("device") or "og",
            "design": data.get("design") or "household",
            "display_id": BUILTIN_DASHBOARD_ID,
            "last_push_at": data.get("last_push_at"),
            "last_push_ok": data.get("last_push_ok"),
            "last_push_message": data.get("last_push_message"),
            "last_push_status": data.get("last_push_status"),
        },
        "widgets": widgets,
        "layout": data.get("layout"),  # keep for TRMNL liquid until custom designs migrate
        "device": data.get("device") or "og",
        "design": data.get("design") or "household",
    }


def load_displays() -> list[dict[str, Any]]:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        migrated = migrate_from_legacy_display_json()
        widgets = (migrated or {}).get("widgets")
        displays = [_builtin_dashboard(widgets)]
        save_displays(displays)
        # Also write a marker so destinations can import TRMNL settings
        if migrated:
            marker = path.parent / "migration_trmnl.json"
            if not marker.exists():
                marker.write_text(
                    json.dumps({k: v for k, v in migrated.items() if k != "widgets"}, indent=2),
                    encoding="utf-8",
                )
        return displays
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("displays") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
    except (OSError, json.JSONDecodeError):
        items = []
    displays = []
    for item in items:
        if isinstance(item, dict):
            normalized = _normalize_display(item)
            if normalized:
                displays.append(normalized)
    if not any(d["id"] == BUILTIN_DASHBOARD_ID for d in displays):
        displays.insert(0, _builtin_dashboard())
    else:
        for d in displays:
            if d["id"] == BUILTIN_DASHBOARD_ID:
                d["builtin"] = True
    return displays


def save_displays(displays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for item in displays:
        n = _normalize_display(item) if isinstance(item, dict) else None
        if n:
            normalized.append(n)
    if not any(d["id"] == BUILTIN_DASHBOARD_ID for d in normalized):
        normalized.insert(0, _builtin_dashboard())
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"displays": normalized}, indent=2), encoding="utf-8")
    return load_displays()


def get_display(display_id: str) -> dict[str, Any] | None:
    target = str(display_id or "").strip()
    for item in load_displays():
        if item["id"] == target:
            return item
    return None


def upsert_display(display: dict[str, Any]) -> dict[str, Any]:
    with _rmw_lock:
        return _upsert_display(display)


def _upsert_display(display: dict[str, Any]) -> dict[str, Any]:
    displays = load_displays()
    normalized = _normalize_display(display)
    if not normalized:
        raise ValueError("invalid display")
    found = False
    for i, item in enumerate(displays):
        if item["id"] == normalized["id"]:
            if item.get("builtin"):
                normalized["builtin"] = True
                normalized["id"] = BUILTIN_DASHBOARD_ID
            displays[i] = normalized
            found = True
            break
    if not found:
        if normalized["id"] == BUILTIN_DASHBOARD_ID:
            normalized["builtin"] = True
        displays.append(normalized)
    save_displays(displays)
    return get_display(normalized["id"]) or normalized


def delete_display(display_id: str) -> bool:
    target = str(display_id or "").strip()
    if target == BUILTIN_DASHBOARD_ID:
        return False
    with _rmw_lock:
        displays = [d for d in load_displays() if d["id"] != target]
        save_displays(displays)
    return True


def update_trmnl_status(display_id: str, **fields: Any) -> dict[str, Any] | None:
    """Merge fields into one Display's ``trmnl`` block (push results, template flag)."""
    with _rmw_lock:
        display = get_display(display_id)
        if not display:
            return None
        display["trmnl"] = {**display.get("trmnl", {}), **fields}
        return upsert_display(display)


def available_widgets() -> list[dict[str, Any]]:
    return [w.to_dict() for w in WIDGET_CATALOG]
