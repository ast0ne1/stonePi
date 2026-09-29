"""Render a Display the way TRMNL does, for the admin preview.

The preview renders the same universal template the plugin holds, with the
same merge_variables a push would send, inside the TRMNL Framework page
skeleton (body.environment.trmnl > .screen > .view.view--full) — mirroring
TRMNL's own local preview tool, trmnlp.
"""

from __future__ import annotations

import html
from functools import lru_cache
from typing import Any

from .templates import UNIVERSAL_TEMPLATE

FRAMEWORK_VERSION = "3.2.0"
FRAMEWORK_CSS = f"https://trmnl.com/css/{FRAMEWORK_VERSION}/plugins.css"
FRAMEWORK_JS = f"https://trmnl.com/js/{FRAMEWORK_VERSION}/plugins.js"

# Rendered canvas + framework screen classes per device (from TRMNL's
# /api/models: palette, device, size, density). TRMNL X lays out at 1040×780
# CSS px and the framework scales .screen by its 1.8 pixel ratio, so the page
# it draws is the panel's native 1872×1404.
PREVIEW_SCREENS: dict[str, dict[str, Any]] = {
    "og": {"width": 800, "height": 480, "classes": "screen--1bit screen--og screen--md screen--1x"},
    "v2": {"width": 1872, "height": 1404, "classes": "screen--4bit screen--v2 screen--lg screen--density-2x"},
}


@lru_cache(maxsize=1)
def _template():
    try:
        from liquid import Environment
    except ImportError as exc:  # pragma: no cover - depends on the app venv
        raise RuntimeError("Display preview needs python-liquid (pip install python-liquid)") from exc
    return Environment().from_string(UNIVERSAL_TEMPLATE)


def render_markup(variables: dict[str, Any]) -> str:
    """Render the universal template with a push payload (merge_variables)."""
    return _template().render(**variables)


# Tells the builder (parent window) where the widget grid landed, so its
# drag overlay lines up with the rendered cells. Sandboxed iframe: postMessage only.
_GEOMETRY_SCRIPT = """<script>
(function () {
  function send() {
    var el = document.querySelector(".view .layout");
    if (!el || !window.parent) return;
    var r = el.getBoundingClientRect();
    window.parent.postMessage({ type: "stonepi-preview-geometry", x: r.left, y: r.top, w: r.width, h: r.height }, "*");
  }
  window.addEventListener("load", function () { send(); setTimeout(send, 250); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(send);
})();
</script>"""


def preview_document(variables: dict[str, Any], *, device: str = "og", report_geometry: bool = True) -> str:
    """Full HTML page for an iframe ``srcdoc``: the device screen at native CSS size."""
    screen = PREVIEW_SCREENS.get(device, PREVIEW_SCREENS["og"])
    body = render_markup(variables)
    geometry = _GEOMETRY_SCRIPT if report_geometry else ""
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8" />
<meta name="trmnl-framework-version" content="{FRAMEWORK_VERSION}" />
<link rel="stylesheet" href="{html.escape(FRAMEWORK_CSS)}" />
<script src="{html.escape(FRAMEWORK_JS)}"></script>
<style>html,body{{margin:0;width:{screen['width']}px;height:{screen['height']}px;overflow:hidden;background:#fff}}</style>
</head>
<body class="environment trmnl">
<div class="screen {screen['classes']}">
<div class="view view--full">
{body}
</div>
</div>
{geometry}
</body></html>
"""
