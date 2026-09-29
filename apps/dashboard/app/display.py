"""Host metrics and the Watch rollup for Dashboard pages.

TRMNL Displays (layout, preview, push) live in Notify; this module keeps the
host readers and Watch snapshot that Health, Services and automations use.
"""

from __future__ import annotations

import stonepi_display.variables
import stonepi_watch
from stonepi_auth import APP_CATALOG

from app.config import DATA_DIR
from app import services

# Host readers + backup formatting live in stonepi_display.variables (shared
# with Notify). Module-level names stay so tests can monkeypatch them.
_read_cpu_pct = stonepi_display.variables.read_cpu_pct
_read_mem_pct = stonepi_display.variables.read_mem_pct
_read_temp_c = stonepi_display.variables.read_temp_c
_read_uptime = stonepi_display.variables.read_uptime
_relative_when = stonepi_display.variables.relative_when
_backup_fields = stonepi_display.variables.backup_fields


def watch_snapshot(cookies: dict[str, str] | None = None) -> dict:
    disabled: set[str] = set()
    try:
        raw = services.auth_request("GET", "/api/apps", cookies or {})
        if isinstance(raw, dict):
            disabled = {str(x) for x in (raw.get("disabled") or [])}
    except Exception:
        pass
    return stonepi_watch.evaluate(
        health_url_for=services.health_url,
        backup_info=services.backup_info,
        disabled_ids=disabled,
        data_dir=DATA_DIR,
        catalog=list(APP_CATALOG),
    )
