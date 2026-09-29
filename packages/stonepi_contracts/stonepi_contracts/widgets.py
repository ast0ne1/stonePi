from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Grid footprint (columns, rows) of each widget size on a Display.
WIDGET_SIZES: dict[str, tuple[int, int]] = {
    "small": (1, 1),
    "medium": (2, 1),
    "large": (2, 2),
    "wide": (4, 1),
}


@dataclass
class WidgetDescriptor:
    id: str
    app: str
    label: str
    blurb: str = ""
    sizes: tuple[str, ...] = ("small", "medium", "large")
    supports_url: bool = False
    supports_qr: bool = False
    # Block id in the pre-Display TRMNL editor; used only to migrate old layouts.
    legacy_block: str = ""
    # Short code carried in the TRMNL layout payload (keeps merge_variables small).
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WIDGET_CATALOG: tuple[WidgetDescriptor, ...] = (
    WidgetDescriptor(
        id="stonepi.system_status",
        code="sys",
        sizes=("medium", "large", "wide"),
        app="stonepi",
        label="System status",
        blurb="Hostname, CPU, RAM, temp, uptime",
        legacy_block="system",
    ),
    WidgetDescriptor(
        id="stonepi.watch",
        code="wat",
        app="stonepi",
        label="Watch",
        blurb="Healthy / attention / critical rollup",
        legacy_block="watch",
    ),
    WidgetDescriptor(
        id="stonepi.apps",
        code="app",
        sizes=("medium", "large"),
        app="stonepi",
        label="Apps",
        blurb="Installed / running pills",
        legacy_block="apps",
    ),
    WidgetDescriptor(
        id="stonepi.storage",
        code="sto",
        app="stonepi",
        label="Storage",
        blurb="Disk used and last backup",
        legacy_block="storage",
    ),
    WidgetDescriptor(
        id="newscast.headlines",
        code="nws",
        app="newscast",
        label="Headlines",
        blurb="Feed count and last update",
        supports_url=True,
        legacy_block="newscast",
    ),
    WidgetDescriptor(
        id="eventtrakr.next_event",
        code="evt",
        app="eventtrakr",
        label="Next event",
        blurb="Next favourite event",
        supports_url=True,
        legacy_block="eventtrakr",
    ),
    WidgetDescriptor(
        id="fileserve.published_files",
        code="fil",
        app="fileserve",
        label="Published files",
        blurb="Hosted page count",
        supports_url=True,
        supports_qr=True,
        legacy_block="fileserve",
    ),
    WidgetDescriptor(
        id="pinboard.recent",
        code="pin",
        app="pinboard",
        label="Recent pins",
        blurb="Notices and reminders",
        legacy_block="pinboard",
    ),
    WidgetDescriptor(
        id="pricewatch.watchlist",
        code="prw",
        app="pricewatch",
        label="Watchlist",
        blurb="Active watches and recent strikes",
        supports_url=True,
    ),
    WidgetDescriptor(
        id="sportguide.next_matches",
        code="spt",
        app="sportguide",
        label="Next matches",
        blurb="What's on next",
        supports_url=True,
    ),
)

_BY_ID = {w.id: w for w in WIDGET_CATALOG}


_BY_CODE = {w.code: w for w in WIDGET_CATALOG if w.code}


def widget_by_id(widget_id: str) -> WidgetDescriptor | None:
    return _BY_ID.get(str(widget_id or "").strip())


def widgets_for_app(app_id: str) -> list[WidgetDescriptor]:
    app = str(app_id or "").strip()
    return [w for w in WIDGET_CATALOG if w.app == app]


def widget_by_code(code: str) -> WidgetDescriptor | None:
    return _BY_CODE.get(str(code or "").strip())
