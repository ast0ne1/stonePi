"""StonePi Notify — ntfy delivery, personal alerts, event ingest, history, destinations."""

from __future__ import annotations

from .destinations import (
    DEFAULT_NTFY_SERVER,
    VAULT_NTFY_TOKEN_KEY,
    load_destinations,
    save_destinations,
    configure as configure_destinations,
    vault_ntfy_token,
)
from .history import append_history, load_history
from .ingest import (
    effective_audience,
    event_approval,
    ingest_event,
    load_needs_review,
    load_prefs,
    normalize_approval,
    plan_recipients,
    roster,
    save_prefs,
)
from .ntfy import deliver_ntfy, publish_ntfy, send_test_notification
from .service import configure, start_scheduler
from .subscriptions import (
    disable_subscription,
    enable_subscription,
    generate_topic,
    get_subscription,
    in_quiet_hours,
    load_subscriptions,
    redact_topic,
    rotate_topic,
    save_subscription,
)

__all__ = [
    "DEFAULT_NTFY_SERVER",
    "VAULT_NTFY_TOKEN_KEY",
    "append_history",
    "configure",
    "configure_destinations",
    "deliver_ntfy",
    "disable_subscription",
    "effective_audience",
    "enable_subscription",
    "event_approval",
    "generate_topic",
    "get_subscription",
    "in_quiet_hours",
    "ingest_event",
    "load_destinations",
    "load_history",
    "load_needs_review",
    "load_prefs",
    "load_subscriptions",
    "normalize_approval",
    "plan_recipients",
    "publish_ntfy",
    "redact_topic",
    "roster",
    "rotate_topic",
    "save_destinations",
    "save_prefs",
    "save_subscription",
    "send_test_notification",
    "start_scheduler",
    "vault_ntfy_token",
]
