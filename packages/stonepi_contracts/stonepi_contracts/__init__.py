"""Shared StonePi event, widget, and destination contracts."""

from __future__ import annotations

from .alerts import forget_alerts_status, notifications_url, personal_alerts_status
from .capabilities import (
    CAPABILITY_ACTION,
    CAPABILITY_INPUT,
    CAPABILITY_LINK,
    CAPABILITY_QR,
    DESTINATION_NTFY,
    DESTINATION_STONEPI_WEB,
    DESTINATION_TRMNL,
    DestinationId,
    destination_capabilities,
)
from .catalog import (
    APP_LABELS,
    EVENT_CATALOG,
    EventType,
    app_label,
    event_type,
    events_for_app,
    grouped_catalog,
)
from .emit import drain_emit_retry_queue, emit_event, notifications_base_url, notify_base_url
from .events import (
    AUDIENCE_ADMIN,
    AUDIENCE_HOUSEHOLD,
    AUDIENCE_PERSONAL,
    AUDIENCES,
    SEVERITIES,
    EventEnvelope,
    normalize_audience,
    normalize_severity,
    normalize_user,
    validate_event,
)
from .widgets import (
    WIDGET_CATALOG,
    WIDGET_SIZES,
    WidgetDescriptor,
    widget_by_code,
    widget_by_id,
    widgets_for_app,
)

__all__ = [
    "APP_LABELS",
    "AUDIENCES",
    "AUDIENCE_ADMIN",
    "AUDIENCE_HOUSEHOLD",
    "AUDIENCE_PERSONAL",
    "CAPABILITY_ACTION",
    "CAPABILITY_INPUT",
    "CAPABILITY_LINK",
    "CAPABILITY_QR",
    "DESTINATION_NTFY",
    "DESTINATION_STONEPI_WEB",
    "DESTINATION_TRMNL",
    "DestinationId",
    "EVENT_CATALOG",
    "EventEnvelope",
    "EventType",
    "SEVERITIES",
    "WIDGET_CATALOG",
    "WIDGET_SIZES",
    "WidgetDescriptor",
    "app_label",
    "destination_capabilities",
    "drain_emit_retry_queue",
    "emit_event",
    "event_type",
    "events_for_app",
    "forget_alerts_status",
    "grouped_catalog",
    "normalize_audience",
    "normalize_severity",
    "normalize_user",
    "notifications_base_url",
    "notifications_url",
    "notify_base_url",
    "personal_alerts_status",
    "validate_event",
    "widget_by_code",
    "widget_by_id",
    "widgets_for_app",
]
