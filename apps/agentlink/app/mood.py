"""Pebble's base mood from StonePi and its owner's day (pebble.md, mood redesign). No AI.

First match wins:

    1 upset    something critical (Auth down, backup failed, disk full): at once
    2 worried  a problem lasting WORRY_AFTER_S, in core StonePi or an app the owner uses
    3 sleepy   battery under LOW_BATTERY, or household quiet hours (Sidekick, default 22:00–07:00)
    4 proud    answered a question in the last PROUD_S
    5 curious  a Pinboard notice under 6 h old, or today's paper out in the last hour
    6 happy    an event later today, or a chat in the last 3 h
    7 content  nothing notable

Overdue reminders don't change the face; they become the caption. A new mood must hold for
SETTLE_S before the face changes (upset and proud show at once), so a blip doesn't flicker.
The reason is what Pebble says when asked how it is, so face and words agree.

Owner signals (events, reminders, notices, briefing) are read only through tools switched on
for this Pebble and apps its owner has. They're device state, not agent actions, so they
aren't audited, and they're cached per device for SIGNAL_CACHE_S so 30 s mood polls stay cheap.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from typing import Any

from app import settings, status

MOODS = ("happy", "content", "sleepy", "curious", "worried", "upset", "proud")
CAPTIONS = {
    "happy": "All good at home",
    "content": "",
    "sleepy": "Quiet time",
    "curious": "Something new",
    "worried": "Something needs a look",
    "upset": "StonePi needs help",
    "proud": "Glad I could help",
}
WORRY_AFTER_S = 5 * 60
PROUD_S = 5 * 60
CHAT_HAPPY_S = 3 * 3600
NOTICE_FRESH_S = 6 * 3600
PAPER_FRESH_S = 3600
LOW_BATTERY = 20
SETTLE_S = 60
SIGNAL_CACHE_S = 120
CORE_APPS = {"auth", "dashboard", "notify", "agentlink"}

_lock = threading.Lock()
_problem_since: dict[str, float] = {}  # problem text → first seen (Watch is household-wide)
_shown: dict[int, dict[str, Any]] = {}  # binding id → what the face shows and what's waiting
_signal_cache: dict[Any, tuple[float, dict[str, Any]]] = {}


def reset_state() -> None:
    with _lock:
        _problem_since.clear()
        _shown.clear()
        _signal_cache.clear()


# -- inputs ---------------------------------------------------------------------------------


def _owner_apps(binding: dict[str, Any]) -> set[str]:
    from app import people

    return people.user_apps(str(binding.get("user_id") or "")) or set()


def _is_critical(reason: str) -> bool:
    return reason.endswith(" is down") or reason == "Backup failed" or (reason.startswith("Disk ") and reason.endswith("% full"))


def _problems(watch: dict[str, Any], owner_apps: set[str], now: float) -> tuple[list[str], list[str]]:
    """(critical reasons, other reasons that have lasted WORRY_AFTER_S), owner-relevant only."""
    names = {str(a.get("n") or a.get("id")): str(a.get("id") or "") for a in watch.get("apps") or []}
    relevant: list[str] = []
    for reason in watch.get("reasons") or []:
        text = str(reason)
        if text.endswith(" is not running"):
            app_id = names.get(text[: -len(" is not running")], "")
            if app_id and app_id not in CORE_APPS and app_id not in owner_apps:
                continue  # an app this owner doesn't use isn't their worry
        relevant.append(text)
    with _lock:
        for gone in [k for k in _problem_since if k not in relevant]:
            del _problem_since[gone]
        for text in relevant:
            _problem_since.setdefault(text, now)
        lasting = [t for t in relevant if not _is_critical(t) and now - _problem_since[t] >= WORRY_AFTER_S]
    return [t for t in relevant if _is_critical(t)], lasting


def _battery(binding: dict[str, Any]) -> int | None:
    try:
        return int(str(binding.get("battery") or "").strip())
    except ValueError:
        return None


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _signals(binding: dict[str, Any], now: float) -> dict[str, Any]:
    """The owner's day, through the tools this Pebble may use; cached per device."""
    from app import registry, tools

    key = binding.get("id")
    cached = _signal_cache.get(key)
    if cached and now - cached[0] < SIGNAL_CACHE_S:
        return cached[1]
    granted = _owner_apps(binding)

    def allowed(tool: str) -> bool:
        spec = registry.spec(tool)
        return spec is not None and registry.tool_enabled(binding, spec) and spec.requires_app in granted

    fetches = {
        "notices": (tools.get_notices, {}),
        "reminders": (tools.get_reminders, {}),
        "events": (tools.get_events, {"days": 1}),
        "news": (tools.get_news_briefing, {}),
    }
    tool_for = {"notices": "get_notices", "reminders": "get_reminders", "events": "get_events", "news": "get_news_briefing"}
    jobs = {name: tools._pool.submit(fn, binding, params) for name, (fn, params) in fetches.items() if allowed(tool_for[name])}
    out: dict[str, Any] = {"newest_notice": None, "overdue": 0, "next_event": None, "paper_out": None}
    for name, job in jobs.items():
        try:
            data = job.result(timeout=6)
        except Exception:  # noqa: BLE001 - an app down only removes its signal
            continue
        if name == "notices" and data.get("notices"):
            out["newest_notice"] = _ts(data["notices"][0].get("created_at"))
        elif name == "reminders":
            out["overdue"] = sum(1 for r in data.get("reminders") or [] if r.get("overdue"))
        elif name == "events":
            today = date.today().isoformat()
            later = [
                e for e in data.get("events") or []
                if str(e.get("start") or "").startswith(today) and (_ts(e.get("start")) or 0) > now
            ]
            later.sort(key=lambda e: (not e.get("favourite"), str(e.get("start"))))
            out["next_event"] = later[0] if later else None
        elif name == "news" and data.get("paper_ready"):
            try:
                hh, mm = (int(x) for x in str(data.get("publishes_at") or "").split(":"))
                out["paper_out"] = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
            except ValueError:
                pass
    _signal_cache[key] = (now, out)
    return out


# -- the decision -----------------------------------------------------------------------------


def decide(binding: dict[str, Any], *, now: float, local_now: datetime | None = None) -> tuple[str, str, str]:
    """(mood, reason, caption) before smoothing."""
    critical, lasting = _problems(status.watch(), _owner_apps(binding), now)
    if critical:
        return "upset", critical[0], critical[0]
    if lasting:
        return "worried", lasting[0], lasting[0]
    battery = _battery(binding)
    if battery is not None and battery < LOW_BATTERY:
        return "sleepy", f"My battery's at {battery}%, I need charging", "I need charging"
    if settings.in_quiet_hours(local_now):
        return "sleepy", "It's quiet time, so I'm resting", CAPTIONS["sleepy"]
    last_ok = binding.get("last_ask_ok") or 0
    since_ok = now - last_ok if last_ok else None
    if since_ok is not None and since_ok < PROUD_S:
        return "proud", "I just helped you with something", CAPTIONS["proud"]
    sig = _signals(binding, now)
    if sig["newest_notice"] and now - sig["newest_notice"] < NOTICE_FRESH_S:
        return "curious", "There's something new on the pinboard", "New on the pinboard"
    if sig["paper_out"] and 0 <= now - sig["paper_out"] < PAPER_FRESH_S:
        return "curious", "Today's paper has just come out", "Today's paper is out"
    if sig["next_event"]:
        title = str(sig["next_event"].get("title") or "Something")
        at = str(sig["next_event"].get("start") or "")[11:16]
        return "happy", f"Looking forward to {title} at {at} today", f"{title} at {at}"
    if since_ok is not None and since_ok < CHAT_HAPPY_S:
        return "happy", "We've been chatting and everything's fine at home", CAPTIONS["happy"]
    return "content", "All's quiet and StonePi is healthy", CAPTIONS["content"]


def _smooth(binding_id: Any, mood: str, reason: str, caption: str, now: float) -> tuple[str, str, str, float]:
    """What the face shows: a change must hold SETTLE_S, except upset and proud."""
    if not isinstance(binding_id, int):
        return mood, reason, caption, now
    with _lock:
        shown = _shown.get(binding_id)
        if shown is None or shown["mood"] == mood or mood in ("upset", "proud"):
            since = shown["since"] if shown and shown["mood"] == mood else now
            _shown[binding_id] = {"mood": mood, "reason": reason, "caption": caption, "since": since, "next": None}
            return mood, reason, caption, since
        waiting = shown.get("next")
        if not waiting or waiting[0] != mood:
            shown["next"] = (mood, now)
        elif now - waiting[1] >= SETTLE_S:
            _shown[binding_id] = {"mood": mood, "reason": reason, "caption": caption, "since": now, "next": None}
            return mood, reason, caption, now
        return shown["mood"], shown["reason"], shown["caption"], shown["since"]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def compute(binding: dict[str, Any], *, now: float | None = None, local_now: datetime | None = None) -> dict[str, Any]:
    now = now if now is not None else time.time()
    mood, reason, caption, since = _smooth(binding.get("id"), *decide(binding, now=now, local_now=local_now), now)
    overdue = int((_signal_cache.get(binding.get("id")) or (0, {}))[1].get("overdue") or 0)
    if overdue and mood not in ("worried", "upset"):
        caption = f"{_plural(overdue, 'reminder')} overdue"
        reason = f"{reason}. You have {_plural(overdue, 'overdue reminder')}"
    return {
        "mood": mood,
        "since": datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason[:120],
        "caption": caption[:40],
        "config_rev": int(binding.get("rev") or 1),
    }
