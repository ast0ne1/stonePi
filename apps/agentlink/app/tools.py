"""Read tools over the user's own StonePi data (AgentLink Phase 2, muse.md §7.1, §9).

Each calls one app's signed agent endpoint (app/apps.py) for the binding's user. Results are
compact and shaped for speech. ``get_today`` aggregates the others: a section appears only
when that tool is on for the binding and the user has the app; a missing section is left
out, not reported empty, so an agent never learns about apps the user can't see.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any

from app import apps, people

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-tools")


def get_weather(binding: dict[str, Any], _params: dict[str, Any]) -> dict[str, Any]:
    return apps.call("notify", "get_weather", str(binding["user_id"]))


def get_events(binding: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"days": params.get("days") or 7}
    for key in ("when", "category"):
        if str(params.get(key) or "").strip():
            out[key] = str(params[key]).strip()
    return apps.call("eventtrakr", "get_events", str(binding["user_id"]), out)


def search_events(binding: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return apps.call("eventtrakr", "search_events", str(binding["user_id"]), {"query": params["query"]})


def get_reminders(binding: dict[str, Any], _params: dict[str, Any]) -> dict[str, Any]:
    return apps.call("pinboard", "get_reminders", str(binding["user_id"]))


def get_notices(binding: dict[str, Any], _params: dict[str, Any]) -> dict[str, Any]:
    return apps.call("pinboard", "get_notices", str(binding["user_id"]))


def get_news_briefing(binding: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    category = str(params.get("category") or "").strip()
    return apps.call("newscast", "get_news_briefing", str(binding["user_id"]), {"category": category} if category else None)


def search_news(binding: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    extra = {"category": str(params["category"]).strip()} if str(params.get("category") or "").strip() else {}
    return apps.call("newscast", "search_news", str(binding["user_id"]), {"query": params["query"], **extra})


def _today_weather(binding: dict[str, Any]) -> dict[str, Any]:
    weather = get_weather(binding, {})
    if not weather.get("configured") or weather.get("unavailable"):
        return {"unavailable": True}
    return {"place": weather.get("place"), "now": weather.get("now"), "today": weather.get("today")}


def _today_events(binding: dict[str, Any]) -> dict[str, Any]:
    events = get_events(binding, {"days": 1}).get("events") or []
    today = date.today().isoformat()
    todays = [e for e in events if str(e.get("start") or "").startswith(today)]
    return {"count": len(todays), "events": todays[:6]}


def _today_reminders(binding: dict[str, Any]) -> dict[str, Any]:
    rems = get_reminders(binding, {}).get("reminders") or []
    today = date.today().isoformat()
    due = [r for r in rems if r.get("overdue") or r.get("due") == today]
    return {"count": len(due), "reminders": due[:6]}


def _today_news(binding: dict[str, Any]) -> dict[str, Any]:
    news = get_news_briefing(binding, {})
    return {
        "briefing_ready": bool(news.get("paper_ready")),
        "headline_count": int(news.get("story_count") or 0),
        "top_headlines": [h.get("title") for h in (news.get("headlines") or [])[:3]],
    }


# get_today section → (the tool that must be on, the app the user must have or None, fetch)
TODAY_SECTIONS = {
    "weather": ("get_weather", None, _today_weather),
    "events": ("get_events", "eventtrakr", _today_events),
    "reminders": ("get_reminders", "pinboard", _today_reminders),
    "news": ("get_news_briefing", "newscast", _today_news),
}


def get_today(binding: dict[str, Any], _params: dict[str, Any]) -> dict[str, Any]:
    from app import registry

    granted = people.user_apps(str(binding["user_id"])) or set()
    wanted = {
        name: fetch
        for name, (tool, app_id, fetch) in TODAY_SECTIONS.items()
        if (app_id is None or app_id in granted) and (spec := registry.spec(tool)) is not None and registry.tool_enabled(binding, spec)
    }
    futures = {name: _pool.submit(fetch, binding) for name, fetch in wanted.items()}
    out: dict[str, Any] = {"date": date.today().isoformat(), "weekday": datetime.now().strftime("%A")}
    for name, future in futures.items():
        try:
            out[name] = future.result(timeout=8)
        except Exception:  # noqa: BLE001 - one app down leaves the rest of the day
            out[name] = {"unavailable": True}
    return out
