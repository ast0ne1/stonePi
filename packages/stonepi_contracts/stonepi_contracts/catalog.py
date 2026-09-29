"""Shared catalog of StonePi event types (labels + audience).

Notify, the Dashboard Notifications page and apps read this so they agree on
what each event is called and who it is for. Events start **not approved** in
Notify until the admin approves them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .events import AUDIENCE_ADMIN, AUDIENCE_HOUSEHOLD, AUDIENCE_PERSONAL

APP_LABELS: dict[str, str] = {
    "newscast": "NewsCast",
    "eventtrakr": "EventTrakr",
    "pricewatch": "PriceWatch",
    "sportguide": "SportGuide",
    "pricescout": "PriceScout",
    "fileserve": "FileServe",
    "studio": "Studio",
    "pinboard": "Pinboard",
    "system": "System",
}


@dataclass(frozen=True)
class EventType:
    id: str
    app: str
    label: str
    audience: str
    blurb: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EVENT_CATALOG: tuple[EventType, ...] = (
    EventType("newscast.publication_available", "newscast", "Paper published", AUDIENCE_PERSONAL,
              "Your paper is ready to read."),
    EventType("newscast.push_available", "newscast", "Paper sent to reader", AUDIENCE_PERSONAL,
              "Your paper reached your e-reader."),
    EventType("eventtrakr.event_approaching", "eventtrakr", "Favourite event starting", AUDIENCE_PERSONAL,
              "A favourite event is coming up."),
    EventType("eventtrakr.social_discovered", "eventtrakr", "New event from social", AUDIENCE_PERSONAL,
              "A followed account posted a new event."),
    EventType("eventtrakr.social_event_updated", "eventtrakr", "Social event updated", AUDIENCE_PERSONAL,
              "An event from a followed account changed."),
    EventType("eventtrakr.social_cancelled", "eventtrakr", "Social event cancelled", AUDIENCE_PERSONAL,
              "An event from a followed account was cancelled."),
    EventType("pricewatch.target_reached", "pricewatch", "Target price reached", AUDIENCE_PERSONAL,
              "A watched item hit your target price."),
    EventType("pricewatch.price_drop", "pricewatch", "Significant price drop", AUDIENCE_PERSONAL,
              "A watched item dropped in price."),
    EventType("sportguide.watched_match_approaching", "sportguide", "Watched match starting", AUDIENCE_PERSONAL,
              "A team you watch plays soon."),
    EventType("pricescout.publication_released", "pricescout", "New store leaflet", AUDIENCE_HOUSEHOLD,
              "A new supermarket leaflet is out."),
    EventType("fileserve.publication_created", "fileserve", "New page published", AUDIENCE_HOUSEHOLD,
              "Someone published a new FileServe page."),
    EventType("studio.site_published", "studio", "Game or app published", AUDIENCE_HOUSEHOLD,
              "A new Studio game or app is live."),
    EventType("pinboard.reminder_due", "pinboard", "Reminder due", AUDIENCE_PERSONAL,
              "A reminder is due today (yours, or the household's if unassigned)."),
    EventType("pinboard.notice_posted", "pinboard", "New notice", AUDIENCE_HOUSEHOLD,
              "Someone posted a notice."),
    EventType("system.disk_warning", "system", "Disk warning", AUDIENCE_ADMIN,
              "The Pi is running low on disk space."),
)

_BY_ID = {e.id: e for e in EVENT_CATALOG}


def event_type(event_id: str) -> EventType | None:
    return _BY_ID.get(str(event_id or "").strip())


def events_for_app(app_id: str) -> list[EventType]:
    app = str(app_id or "").strip()
    return [e for e in EVENT_CATALOG if e.app == app]


def app_label(app_id: str) -> str:
    app = str(app_id or "").strip()
    return APP_LABELS.get(app, app.replace("_", " ").title() or "Other")


def grouped_catalog(events: list[EventType] | tuple[EventType, ...] | None = None) -> list[dict[str, Any]]:
    """Events grouped by app, in catalog order: ``[{id, label, events: [...]}]``."""
    groups: dict[str, list[EventType]] = {}
    for ev in EVENT_CATALOG if events is None else events:
        groups.setdefault(ev.app, []).append(ev)
    return [{"id": app, "label": app_label(app), "events": evs} for app, evs in groups.items()]
