from __future__ import annotations

from typing import Literal

CAPABILITY_LINK = "link"
CAPABILITY_QR = "qr"
CAPABILITY_ACTION = "action"
CAPABILITY_INPUT = "input"

DESTINATION_STONEPI_WEB = "stonepi_web"
DESTINATION_TRMNL = "trmnl"
DESTINATION_NTFY = "ntfy"

DestinationId = Literal["stonepi_web", "trmnl", "ntfy"]

_DESTINATION_CAPS: dict[str, frozenset[str]] = {
    DESTINATION_STONEPI_WEB: frozenset(
        {CAPABILITY_LINK, CAPABILITY_QR, CAPABILITY_ACTION, CAPABILITY_INPUT}
    ),
    DESTINATION_TRMNL: frozenset({CAPABILITY_QR}),
    DESTINATION_NTFY: frozenset({CAPABILITY_LINK, CAPABILITY_ACTION}),
}


def destination_capabilities(destination_id: str) -> frozenset[str]:
    return _DESTINATION_CAPS.get(str(destination_id or "").strip(), frozenset())
