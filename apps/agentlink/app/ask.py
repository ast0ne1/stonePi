"""One spoken question → one spoken answer (PROTOCOL.md §7, pebble.md).

WAV (16 kHz) → Whisper → context from the binding's read tools → stonepi_ai ``pebble.reply``
→ piper → WAV (22.05 kHz). One ask at a time across the Pi. The audit log records that an
ask happened and how long it took, never what was said.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from stonepi_audit import ALLOWED, FAILED, denied

from app import deps, registry, speech
from app.errors import ApiError
from app.wav import ASK_RATE, BadAudio, level_dbfs, read_pcm16

logger = logging.getLogger("agentlink.ask")

MAX_ASK_S = 15
MAX_BYTES = 512 * 1024
REPLY_MAX_TOKENS = 400
# The device waits 40 s for an ask (PROTOCOL.md §2): the whole ask must answer inside
# ASK_DEADLINE_S, as JSON even when the model is slow. stonepi_ai tries fallback models in
# turn and httpx timeouts are per read, so the AI step gets a wall-clock budget of its own.
ASK_DEADLINE_S = 35.0
AI_MAX_S = 20.0
TTS_RESERVE_S = 5.0  # left for speaking the reply
_ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pebble-ai")
_busy = threading.Lock()

SYSTEM_PROMPT = """You are Pebble, StonePi's sidekick: a small, friendly portable companion with a round screen.
StonePi is the household's home server; you may be with your owner at home or out and about.
Your reply is spoken aloud, so:
- Answer in 1 to 3 short sentences of plain speech, up to 5 when reading out headlines or
  events. No lists, markdown, emoji or URLs.
- Use the household facts below when they're relevant. Never invent household facts.
- If the facts don't cover a question about this household, say you don't know that yet,
  then answer generally if you can.
- When asked how you are or how you feel, answer in character from your mood and its reason
  below (it's what your face shows), not from the household facts in general.
- If a question needs something you can't read yet (marked "off" in the app guide), say so and
  that {owner} or an admin can switch it on in Sidekick. Never claim to read an app you can't.
- News: asked broadly what's in the news when today's briefing has more than one category, say
  how many stories there are and name the categories in one sentence, then ask which one they'd
  like. Asked for one category (now, or as their answer to your question), read the top two or
  three headlines from it (the get_news_briefing:<category> facts). If that category isn't there
  (category_not_found), say so and name the ones there are.
- Events: asked what's on at a time ("tonight", "tomorrow", "next week"), answer from the
  get_events:<when> facts, which cover everything then, starred ones first. If there are more than
  four, give the count, the highlights and the kinds of events, and offer to narrow it down. Asked
  for one kind ("community events"), use the facts filtered to it; if that kind isn't there
  (category_not_found), say so and name the kinds there are.
- Weather: answer from the get_weather facts, for home (name the place if they ask where). Give
  what they asked for: now, later today (next_hours) or a day ahead (next_days), with the rain
  chance when it matters. If it isn't set up (configured false), say an admin can pick a place in
  Notify. Asked about somewhere else, say you only know the weather at home.
You're talking to {owner}. It's {now}.
{previous}
About you:
{about}

What StonePi has for {owner}, and what you can read (app guide):
{guide}

Household facts (JSON, from StonePi just now):
{facts}"""

HEADER_MAX_BYTES = 200  # PROTOCOL.md §7: text headers are at most 200 bytes once encoded
SPOKEN_MAX_CHARS = 900


def header_text(text: str) -> str:
    """The caption header: whole words, with "…" when it doesn't all fit (the audio has it all)."""
    encoded = quote(text, safe=" !'()*,-.:;?")
    if len(encoded) <= HEADER_MAX_BYTES:
        return encoded
    words = text.split()
    while words and len(quote(" ".join(words) + "…", safe=" !'()*,-.:;?")) > HEADER_MAX_BYTES:
        words.pop()
    return quote(" ".join(words).rstrip(",;:") + "…", safe=" !'()*,-.:;?")


def _whole_sentences(text: str, limit: int) -> str:
    """Up to ``limit`` characters, ending on a full sentence when there's one to end on."""
    if len(text) <= limit and re.search(r"[.!?]['\")]?$", text):
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?]['\")]?(?=\s|$)", cut)]
    if ends:
        return cut[: ends[-1]].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "." if " " in cut else cut


def speakable(text: str, *, cut_short: bool = False) -> str:
    """Strip what TTS would read out literally (markdown, URLs, stray symbols); never stop
    mid-sentence, even when the model ran out of tokens (``cut_short``)."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if cut_short or len(text) > SPOKEN_MAX_CHARS:
        return _whole_sentences(text, SPOKEN_MAX_CHARS)
    return text


FACTS_MAX_CHARS = 9000
PLANNER_MAX_S = 6.0
SEARCH_TOOLS = ("search_events", "search_news")
_tool_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="pebble-facts")

PLANNER_PROMPT = """You pick look-ups for a voice assistant. Given the user's question, decide which
of these look-ups over their own StonePi data would help answer it:
{tools}
Only search when the question asks about a specific topic, person, place or kind of event to
look up (e.g. "anything about Mars in the news?", "is there jazz this week?"). Questions about
StonePi itself, the day, reminders, or general knowledge need no search: use "".
If the user is answering the assistant's previous question (shown below the question), use
their answer: "sport" after "which category?" means news_category "sport", and "just the
community ones" after an answer about tonight's events means events_when "tonight" and
events_category "community".
Reply with JSON only, one key per look-up and the words to use, or "" when it wouldn't help.
Example: {example}"""

NEWS_CATEGORY_HINT = (
    "- news_category: when they want the news from one category or section (e.g. \"what's in the "
    "news in sport?\", \"any tech news?\"), that category in a word or two; otherwise \"\"."
)
EVENT_HINTS = (
    "- events_when: when they ask what's on at a time, one of today, tonight, tomorrow, this_weekend, "
    "this_week, next_week; otherwise \"\".",
    "- events_category: when they ask for one kind of event (e.g. \"community events\", \"any gigs?\" → "
    "music), that kind in a word or two; otherwise \"\".",
)
FOLLOWUP_S = 120.0
_recent: dict[int, tuple[float, str, str]] = {}  # binding id → (when, question, answer); RAM only
_recent_lock = threading.Lock()


def _previous(binding: dict[str, Any]) -> tuple[str, str] | None:
    """The last question and answer on this Pebble, if it was under FOLLOWUP_S ago."""
    with _recent_lock:
        row = _recent.get(binding.get("id"))
    if row and time.monotonic() - row[0] < FOLLOWUP_S:
        return row[1], row[2]
    return None


def _remember(binding: dict[str, Any], question: str, answer: str) -> None:
    with _recent_lock:
        now = time.monotonic()
        for key in [k for k, row in _recent.items() if now - row[0] >= FOLLOWUP_S]:
            del _recent[key]
        _recent[binding["id"]] = (now, question[:300], answer[:600])


def _plan_searches(
    binding: dict[str, Any], transcript: str, search_specs: list, news: bool = False, events: bool = False,
) -> dict[str, str]:
    """{look-up: words} chosen by a quick model call; {} when nothing fits or it fails.

    Keys are search tool names, plus ``news_category`` when the briefing tool is on and
    ``events_when`` / ``events_category`` when get_events is.
    """
    try:
        import stonepi_ai
    except ImportError:
        return {}
    lines = [f"- {s.name}: {s.description}" for s in search_specs] + ([NEWS_CATEGORY_HINT] if news else [])
    lines += list(EVENT_HINTS) if events else []
    allowed = {s.name for s in search_specs} | ({"news_category"} if news else set())
    allowed |= {"events_when", "events_category"} if events else set()
    example = json.dumps({key: "" for key in sorted(allowed)})
    prompt = transcript
    previous = _previous(binding)
    if previous:
        prompt = f"{transcript}\n\n(The assistant had just said: {previous[1]})"
    try:
        result = stonepi_ai.complete(
            "pebble.reply", system=PLANNER_PROMPT.format(tools="\n".join(lines), example=example), prompt=prompt,
            max_tokens=60, temperature=0, timeout=httpx.Timeout(PLANNER_MAX_S, connect=3.0), alert=False,
        )
        match = re.search(r"\{.*\}", result.text, re.S)
        plan = json.loads(match.group(0)) if match else {}
    except Exception:  # noqa: BLE001 - no plan is fine: the answer just uses the other facts
        return {}
    return {k: str(v).strip()[:80] for k, v in plan.items() if k in allowed and isinstance(v, str) and v.strip()}


def _facts(binding: dict[str, Any], transcript: str = "") -> dict[str, Any]:
    """What Pebble may know for this question, each fetch through invoke (policy, grants, audit).

    Every enabled read tool without required params runs, in parallel. get_today is skipped
    when the tools it's made of are on (it would only repeat them). When a search tool is on,
    a quick model call picks searches for this question, alongside the other fetches.
    """
    enabled = registry.tools_for(binding)
    names = {s.name for s in enabled}
    plain = [
        s for s in enabled
        if s.tier == registry.Tier.READ and not any(p.required for p in s.params) and s.name not in SEARCH_TOOLS
    ]
    if "get_today" in names and names & {"get_events", "get_reminders", "get_news_briefing", "get_weather"}:
        plain = [s for s in plain if s.name != "get_today"]
    searches = [s for s in enabled if s.name in SEARCH_TOOLS]

    news = "get_news_briefing" in names
    events = "get_events" in names
    futures = {s.name: _tool_pool.submit(registry.invoke, binding, s.name, {}, "ask") for s in plain}
    plan_future = (
        _tool_pool.submit(_plan_searches, binding, transcript, searches, news, events)
        if (searches or news or events) and transcript else None
    )
    facts: dict[str, Any] = {}
    if plan_future is not None:
        try:
            plan = plan_future.result(timeout=PLANNER_MAX_S + 1)
        except Exception:  # noqa: BLE001
            plan = {}
        wanted = {k: plan.pop(k) for k in ("events_when", "events_category") if k in plan}
        if wanted:
            # The next week is already coming; this is the window or kind they asked about.
            params = {"when": wanted.get("events_when", ""), "category": wanted.get("events_category", "")}
            if not params["when"]:
                params["days"] = 14
            label = "/".join(v for v in (params["when"], params["category"]) if v)
            futures[f"get_events:{label}"] = _tool_pool.submit(
                registry.invoke, binding, "get_events", {k: v for k, v in params.items() if v}, "ask")
        for tool, query in plan.items():
            if tool == "news_category":
                # The whole briefing is already coming; this narrows it to what they asked for.
                futures[f"get_news_briefing:{query}"] = _tool_pool.submit(
                    registry.invoke, binding, "get_news_briefing", {"category": query}, "ask")
            else:
                futures[f"{tool}:{query}"] = _tool_pool.submit(registry.invoke, binding, tool, {"query": query}, "ask")
    for key, future in futures.items():
        try:
            result = future.result(timeout=10)
        except Exception:  # noqa: BLE001 - a slow app leaves the rest of the facts
            continue
        if result.get("ok"):
            facts[key] = result["payload"]
    return facts


def _about(binding: dict[str, Any]) -> str:
    """Pebble's own state, so "how are you?" matches the face."""
    from app import mood, settings

    lines = [f"- Your name is {(binding.get('settings') or {}).get('nickname') or 'Pebble'}; you belong to "
             f"{binding.get('user_name') or 'someone in the household'}."]
    try:
        now = mood.compute(binding)
        lines.append(f"- Your mood is {now['mood']} because: {now['reason']}.")
    except Exception:  # noqa: BLE001 - a mood is nice to have, not needed to answer
        pass
    battery = str(binding.get("battery") or "").strip()
    if battery:
        lines.append(f"- Your battery is at {battery}%.")
    where = binding.get("last_path")
    if where in ("lan", "away"):
        lines.append("- You're at home, on the household Wi-Fi." if where == "lan" else "- You're away from home, reaching StonePi over the internet.")
    quiet = settings.quiet_hours()
    if quiet["enabled"]:
        lines.append(f"- Household quiet hours are {quiet['start']} to {quiet['end']}; you're sleepy then.")
    return "\n".join(lines)


def _app_guide(binding: dict[str, Any]) -> str:
    """The owner's apps in a line each, with which of your tools read them and whether they're on."""
    from app import people

    try:
        from stonepi_auth import APP_CATALOG
    except ImportError:
        return "- StonePi: the household's home server. get_status (on): its health."
    granted = people.user_apps(str(binding.get("user_id") or "")) or set()
    by_app: dict[str, list[str]] = {}
    for spec in registry.TOOLS:
        state = "on" if registry.tool_enabled(binding, spec) else "off"
        by_app.setdefault(spec.requires_app or "", []).append(f"{spec.name} ({state})")
    lines = [f"- StonePi itself: {', '.join(by_app.get('', []))}. get_status is its health; get_weather is the "
             "weather at home; get_today sums up the day."]
    for item in APP_CATALOG:
        app_id = str(item.get("id") or "")
        if item.get("group") == "system" or app_id not in granted:
            continue
        reads = by_app.get(app_id)
        lines.append(
            f"- {item.get('name') or app_id}: {str(item.get('description') or '').rstrip('.')}. "
            + (f"You can read it with {', '.join(reads)}." if reads else "You can't read this app yet.")
        )
    return "\n".join(lines)


def _previous_text(binding: dict[str, Any]) -> str:
    previous = _previous(binding)
    if not previous:
        return ""
    return (f"\nA moment ago they asked: \"{previous[0]}\" and you answered: \"{previous[1]}\". "
            "If this question follows on from that (for example it answers your question), continue it.\n")


def _reply_text(binding: dict[str, Any], transcript: str, budget_s: float = AI_MAX_S) -> str:
    try:
        import stonepi_ai
    except ImportError as exc:
        raise ApiError(503, "ai_unavailable", "AI isn't set up on StonePi", 60) from exc
    system = SYSTEM_PROMPT.format(
        owner=binding.get("user_name") or "someone in the household",
        now=datetime.now().strftime("%A %d %B, %H:%M"),
        previous=_previous_text(binding),
        about=_about(binding),
        guide=_app_guide(binding),
        facts=json.dumps(_facts(binding, transcript), ensure_ascii=False, default=str)[:FACTS_MAX_CHARS],
    )
    if budget_s < 3:
        raise ApiError(503, "ai_unavailable", "That took too long, try again", 5)
    future = _ai_pool.submit(
        stonepi_ai.complete,
        "pebble.reply", system=system, prompt=transcript, max_tokens=REPLY_MAX_TOKENS, temperature=0.4,
        timeout=httpx.Timeout(min(AI_MAX_S, budget_s), connect=5.0),
    )
    try:
        # A model still trying after this finishes in the background; its answer is dropped.
        result = future.result(timeout=budget_s)
    except FutureTimeout as exc:
        logger.warning("pebble.reply ran past its %.0f s budget", budget_s)
        raise ApiError(503, "ai_unavailable", "That took too long, try again", 5) from exc
    except stonepi_ai.AIUnavailable as exc:
        logger.warning("pebble.reply unavailable: %s", exc)
        raise ApiError(503, "ai_unavailable", "AI isn't set up on StonePi", 60) from exc
    text = speakable(result.text, cut_short=getattr(result, "stop_reason", "") == "max_tokens")
    if not text:
        raise ApiError(503, "ai_unavailable", "AI gave no answer", 30)
    return text


def run(binding: dict[str, Any], body: bytes, content_type: str) -> tuple[bytes, dict[str, str]]:
    """The whole ask. Raises ApiError for every contract error."""
    ctype = content_type.split(";")[0].strip().lower()
    if ctype not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise ApiError(415, "unsupported_audio", "Send 16 kHz mono 16-bit WAV")
    try:
        samples, _rate = read_pcm16(body, rate=ASK_RATE)
    except BadAudio as exc:
        raise ApiError(415, "unsupported_audio", "Send 16 kHz mono 16-bit WAV") from exc
    seconds = len(samples) / ASK_RATE
    if seconds > MAX_ASK_S + 0.5:
        raise ApiError(413, "too_large", "That was too long")
    if seconds < 0.3 or level_dbfs(samples) < -60:
        raise ApiError(422, "no_speech", "Didn't catch that")
    if not _busy.acquire(blocking=False):
        raise ApiError(409, "busy", "Pebble is already answering", 5)
    started = time.monotonic()
    timings: dict[str, int] = {}
    decision = FAILED
    store = deps.store()
    try:
        t = time.monotonic()
        try:
            transcript = speech.transcribe(samples)
        except speech.SpeechUnavailable as exc:
            raise ApiError(503, "speech_unavailable", "Speech isn't working right now", 60) from exc
        timings["stt_ms"] = int((time.monotonic() - t) * 1000)
        if not transcript:
            decision = denied("no_speech")
            raise ApiError(422, "no_speech", "Didn't catch that")
        t = time.monotonic()
        budget = min(AI_MAX_S, ASK_DEADLINE_S - (time.monotonic() - started) - TTS_RESERVE_S)
        reply = _reply_text(binding, transcript, budget)
        timings["ai_ms"] = int((time.monotonic() - t) * 1000)
        t = time.monotonic()
        try:
            wav = speech.synthesize(reply)
        except speech.SpeechUnavailable as exc:
            raise ApiError(503, "speech_unavailable", "Speech isn't working right now", 60) from exc
        timings["tts_ms"] = int((time.monotonic() - t) * 1000)
        decision = ALLOWED
        _remember(binding, transcript, reply)
        headers = {
            "X-Pebble-Transcript": header_text(transcript),
            "X-Pebble-Reply": header_text(reply),
            "X-Pebble-Mood": "proud",
        }
        return wav, headers
    except ApiError as exc:
        if decision == FAILED and exc.error == "no_speech":
            decision = denied("no_speech")
        raise
    finally:
        _busy.release()
        store.mark_ask(binding["id"], ok=decision == ALLOWED)
        deps.audit().record(
            actor=f"binding:{binding['id']}", subject=str(binding.get("user_id") or ""), action="ask",
            decision=decision, params={"seconds": round(seconds, 1), **timings},
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        logger.info("ask binding=%s %.1fs %s %s", binding["id"], seconds, decision, timings)
