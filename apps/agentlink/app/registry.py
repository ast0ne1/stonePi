"""The tool registry and ``invoke``: every adapter's way in (muse.md §4.1, §7).

    invoke(binding, tool, params, request_id) → {ok, payload} | {ok: false, error}

Checks, in order: binding live → tool known → params valid → binding policy → owner still
enabled → run → audit. Nothing here knows about Pebble, Muse or MCP.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from stonepi_audit import ALLOWED, FAILED, denied
from stonepi_contracts.agent import Param, Tier, ToolSpec, fail, ok, registry_rev, validate_params

from app import apps, deps, people, status, tools

ToolFn = Callable[[dict[str, Any], dict[str, Any]], Any]  # (binding, params) → payload

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_status",
        "agentlink",
        Tier.READ,
        "StonePi's health right now: overall level (healthy, attention, critical), what needs "
        "attention, services that are down, disk use and days since the last backup. No personal data.",
        default_enabled=True,
    ),
    ToolSpec(
        "get_weather",
        "notify",
        Tier.READ,
        "The weather at home (the household's place, set in Notify): now, the rest of today every "
        "3 hours, and the next 3 days, with rain chances. The same for everyone; no personal data.",
        default_enabled=True,
    ),
    # Personal data: off for a new binding (muse.md §15); each re-checks the user's app grant.
    ToolSpec(
        "get_today",
        "agentlink",
        Tier.READ,
        "The owner's day at a glance: today's weather, events, reminders due today or overdue, and whether "
        "today's news briefing is ready with its top headlines. Only includes parts whose own tool is on.",
    ),
    ToolSpec(
        "get_events",
        "eventtrakr",
        Tier.READ,
        "The owner's upcoming events from EventTrakr (their Agenda): title, day and time, place, "
        "category, price and whether they starred it, plus the window's categories with counts. "
        "Soonest first; favourites kept when there are many.",
        params=(
            Param("days", "integer", "How many days ahead, 1 to 31 (default 7)", required=False),
            Param("when", "string", "today, tonight, tomorrow, this_weekend, this_week or next_week (instead of days)", required=False),
            Param("category", "string", "Only this kind of event, e.g. 'community' or 'music'", required=False),
        ),
        requires_app="eventtrakr",
    ),
    ToolSpec(
        "search_events",
        "eventtrakr",
        Tier.READ,
        "Search the owner's EventTrakr events by words (title, place, category, source). Best matches first.",
        params=(Param("query", "string", "Words to look for, e.g. 'jazz' or 'Tivoli'"),),
        requires_app="eventtrakr",
    ),
    ToolSpec(
        "get_reminders",
        "pinboard",
        Tier.READ,
        "Pinboard reminders for the owner plus unassigned household ones, with due dates and whether "
        "they're overdue. Never reminders assigned to someone else.",
        requires_app="pinboard",
    ),
    ToolSpec(
        "get_notices",
        "pinboard",
        Tier.READ,
        "The household's Pinboard notices, newest first.",
        requires_app="pinboard",
    ),
    ToolSpec(
        "get_news_briefing",
        "newscast",
        Tier.READ,
        "The owner's NewsCast briefing for today: whether the daily paper is ready, how many stories, "
        "today's categories with how many stories each, and the top headlines with source, category "
        "and a short summary. Give a category to hear only that one.",
        params=(Param("category", "string", "Only this category, e.g. 'sport' or 'tech'", required=False),),
        requires_app="newscast",
    ),
    ToolSpec(
        "search_news",
        "newscast",
        Tier.READ,
        "Search the owner's recent NewsCast stories by words. Newest first, with short summaries.",
        params=(
            Param("query", "string", "Words to look for, e.g. 'election' or 'Mars'"),
            Param("category", "string", "Only this category, e.g. 'sport'", required=False),
        ),
        requires_app="newscast",
    ),
)

_HANDLERS: dict[str, ToolFn] = {
    "get_status": lambda _binding, _params: status.get_status_payload(),
    "get_weather": tools.get_weather,
    "get_today": tools.get_today,
    "get_events": tools.get_events,
    "search_events": tools.search_events,
    "get_reminders": tools.get_reminders,
    "get_notices": tools.get_notices,
    "get_news_briefing": tools.get_news_briefing,
    "search_news": tools.search_news,
}

_BY_NAME = {spec.name: spec for spec in TOOLS}
REGISTRY_REV = registry_rev(TOOLS)


def spec(name: str) -> ToolSpec | None:
    return _BY_NAME.get(name)


def tool_enabled(binding: dict[str, Any], tool: ToolSpec) -> bool:
    choice = (binding.get("policy") or {}).get(tool.name)
    if choice in ("allow", "deny"):
        return choice == "allow"
    return tool.default_enabled


def tools_for(binding: dict[str, Any]) -> list[ToolSpec]:
    """Tools this binding may call (what an adapter advertises to its agent)."""
    return [t for t in TOOLS if tool_enabled(binding, t)]


def invoke(binding: dict[str, Any] | None, tool: str, params: dict[str, Any] | None = None, request_id: str = "") -> dict[str, Any]:
    started = time.monotonic()
    actor = f"binding:{binding['id']}" if binding else "binding:?"
    subject = str((binding or {}).get("user_id") or "")

    def record(decision: str, size: int | None = None) -> None:
        deps.audit().record(
            actor=actor, subject=subject, action=tool, decision=decision, params=params,
            size=size, duration_ms=int((time.monotonic() - started) * 1000), note=request_id,
        )

    if not binding or binding.get("state") != "BOUND":
        record(denied("binding"))
        return fail("unauthorized", "This device isn't paired.")
    spec_ = _BY_NAME.get(tool)
    if spec_ is None:
        record(denied("unknown_tool"))
        return fail("unknown_tool", f"No tool called {tool}.")
    clean, why = validate_params(spec_, params)
    if clean is None:
        record(denied("params"))
        return fail("bad_params", why)
    if not tool_enabled(binding, spec_):
        record(denied("policy"))
        return fail("not_allowed", f"{tool} is switched off for this device.")
    enabled = people.user_enabled(subject)
    if enabled is False or (enabled is None and spec_.requires_app):
        record(denied("owner"))
        return fail("owner_disabled", "This device's owner no longer has access.")
    if spec_.requires_app:
        granted = people.user_apps(subject)
        if granted is None or spec_.requires_app not in granted:
            record(denied("no_app"))
            return fail("no_access", f"The owner doesn't use {spec_.requires_app}.")
    try:
        payload = _HANDLERS[tool](binding, clean)
    except apps.AppUnavailable as exc:
        record(FAILED)
        return fail("app_unavailable", str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool failing is an answer, not a crash
        record(FAILED)
        return fail("tool_failed", str(exc)[:120] or "The tool failed.")
    result = ok(payload)
    record(ALLOWED, size=len(str(payload)))
    return result
