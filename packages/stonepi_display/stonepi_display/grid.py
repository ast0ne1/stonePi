"""Display grid: device cell grids, widget footprints, placement.

A Display's widgets sit on a coarse cell grid (x, y are 1-based column/row).
The footprint of a widget comes from its size (``WIDGET_SIZES``), clamped to
the device grid, so "wide" spans the full width on any device.
"""

from __future__ import annotations

from typing import Any

from stonepi_contracts import WIDGET_SIZES, widget_by_id

# Cell grid per TRMNL device. OG is 800×480 CSS px, TRMNL X ("v2") lays out
# at 1040×780 CSS px — both keep cells roughly 190 px square after the title bar.
DEVICE_GRIDS: dict[str, dict[str, int]] = {
    "og": {"cols": 4, "rows": 3},
    "v2": {"cols": 4, "rows": 4},
}
DEFAULT_GRID_DEVICE = "og"


def device_grid(device: str | None) -> dict[str, int]:
    return dict(DEVICE_GRIDS.get(str(device or ""), DEVICE_GRIDS[DEFAULT_GRID_DEVICE]))


def allowed_sizes(widget_id: str) -> tuple[str, ...]:
    desc = widget_by_id(widget_id)
    sizes = desc.sizes if desc else ("medium",)
    return tuple(s for s in sizes if s in WIDGET_SIZES) or ("medium",)


def footprint(size: str, device: str | None) -> tuple[int, int]:
    grid = device_grid(device)
    w, h = WIDGET_SIZES.get(size, WIDGET_SIZES["medium"])
    return min(w, grid["cols"]), min(h, grid["rows"])


def _cells(x: int, y: int, w: int, h: int) -> set[tuple[int, int]]:
    return {(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}


def _fits(x: int, y: int, w: int, h: int, grid: dict[str, int], taken: set[tuple[int, int]]) -> bool:
    if x < 1 or y < 1 or x + w - 1 > grid["cols"] or y + h - 1 > grid["rows"]:
        return False
    return not (_cells(x, y, w, h) & taken)


def _first_fit(w: int, h: int, grid: dict[str, int], taken: set[tuple[int, int]]) -> tuple[int, int] | None:
    for y in range(1, grid["rows"] + 1):
        for x in range(1, grid["cols"] + 1):
            if _fits(x, y, w, h, grid, taken):
                return x, y
    return None


def place_widgets(widgets: list[dict[str, Any]], device: str | None) -> list[dict[str, Any]]:
    """Validate positions and auto-place the rest.

    Widgets keep a valid, non-overlapping (x, y). Widgets without one — or
    whose spot is off-grid or taken — go to the first free spot. Widgets
    that fit nowhere get x = y = 0 ("unplaced": saved, not drawn).
    """
    grid = device_grid(device)
    taken: set[tuple[int, int]] = set()
    placed: list[dict[str, Any] | None] = [None] * len(widgets)
    pending: list[int] = []
    for i, widget in enumerate(widgets):
        size = widget.get("size") or "medium"
        w, h = footprint(size, device)
        try:
            x, y = int(widget.get("x") or 0), int(widget.get("y") or 0)
        except (TypeError, ValueError):
            x = y = 0
        if x and y and _fits(x, y, w, h, grid, taken):
            taken |= _cells(x, y, w, h)
            placed[i] = {**widget, "x": x, "y": y}
        else:
            pending.append(i)
    for i in pending:
        widget = widgets[i]
        w, h = footprint(widget.get("size") or "medium", device)
        spot = _first_fit(w, h, grid, taken)
        if spot:
            taken |= _cells(spot[0], spot[1], w, h)
            placed[i] = {**widget, "x": spot[0], "y": spot[1]}
        else:
            placed[i] = {**widget, "x": 0, "y": 0}
    return [p for p in placed if p is not None]


def layout_rows(display: dict[str, Any]) -> list[list[Any]]:
    """Compact layout for the TRMNL payload: [[code, x, y, w, h], …] for placed widgets."""
    device = display.get("device")
    rows: list[list[Any]] = []
    for widget in display.get("widgets") or []:
        desc = widget_by_id(widget.get("widget_id") or "")
        if not desc or not desc.code or not widget.get("x") or not widget.get("y"):
            continue
        w, h = footprint(widget.get("size") or "medium", device)
        rows.append([desc.code, int(widget["x"]), int(widget["y"]), w, h])
    rows.sort(key=lambda row: (row[2], row[1]))
    return rows
