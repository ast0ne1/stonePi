"""Weather codes (WMO, as Open-Meteo sends them) to an icon name and a label.

Shared by the Car Thing's weather widgets and Ripple's family display.
"""

from __future__ import annotations

from typing import Any

WMO: dict[int, tuple[str, str]] = {
    0: ("sun", "Clear"), 1: ("sun", "Mostly clear"), 2: ("partly", "Partly cloudy"), 3: ("cloud", "Overcast"),
    45: ("fog", "Fog"), 48: ("fog", "Fog"),
    51: ("rain", "Drizzle"), 53: ("rain", "Drizzle"), 55: ("rain", "Drizzle"), 56: ("rain", "Freezing drizzle"),
    57: ("rain", "Freezing drizzle"), 61: ("rain", "Light rain"), 63: ("rain", "Rain"), 65: ("rain", "Heavy rain"),
    66: ("rain", "Freezing rain"), 67: ("rain", "Freezing rain"), 71: ("snow", "Light snow"), 73: ("snow", "Snow"),
    75: ("snow", "Heavy snow"), 77: ("snow", "Snow grains"), 80: ("rain", "Showers"), 81: ("rain", "Showers"),
    82: ("rain", "Heavy showers"), 85: ("snow", "Snow showers"), 86: ("snow", "Snow showers"),
    95: ("storm", "Thunderstorm"), 96: ("storm", "Thunderstorm"), 99: ("storm", "Thunderstorm"),
}


def wmo(code: Any, is_day: bool = True) -> tuple[str, str]:
    """(icon, label) for a weather code; a clear night gets the moon."""
    try:
        icon, label = WMO.get(int(code), ("cloud", "—"))
    except (TypeError, ValueError):
        icon, label = "cloud", "—"
    if icon == "sun" and not is_day:
        icon = "moon"
    return icon, label
