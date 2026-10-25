"""Phase 2 read tools: app calls per user, grant checks, get_today, the ask's facts and
search planner, the curious mood, and the household voice."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app import apps, ask, deps, mood, people, registry, status, tools, voices
from app.config import env

USER = "11111111-1111-4111-8111-111111111111"
WATCH = {"level": "healthy", "summary": "All clear", "reasons": [], "apps": []}
TODAY = date.today().isoformat()


@pytest.fixture
def binding(tmp_path, monkeypatch):
    deps.reset(tmp_path)
    monkeypatch.setattr(people, "user_enabled", lambda _uid: True)
    monkeypatch.setattr(people, "user_apps", lambda _uid: {"eventtrakr", "pinboard", "newscast"})
    monkeypatch.setattr(status, "watch", lambda: WATCH)
    b, _token = deps.store().create_pebble(device_id="pbl-a0b1c2d3e4f5", user_id=USER, user_name="Adam")
    return b


@pytest.fixture
def app_calls(monkeypatch):
    calls = []
    answers = {
        ("eventtrakr", "get_events"): {"events": [
            {"title": "Jazz", "start": f"{TODAY}T19:00+02:00", "when": "today 19:00"},
            {"title": "Talk", "start": "2999-01-01T10:00+01:00", "when": "later"},
        ]},
        ("eventtrakr", "search_events"): {"events": [{"title": "Jazz"}]},
        ("pinboard", "get_reminders"): {"reminders": [
            {"text": "Bins", "due": TODAY, "overdue": False},
            {"text": "Pay", "due": "2000-01-01", "overdue": True},
            {"text": "Later", "due": "2999-01-01", "overdue": False},
        ]},
        ("pinboard", "get_notices"): {"notices": [{"text": "New", "created_at": datetime.now(timezone.utc).isoformat()}]},
        ("newscast", "get_news_briefing"): {"paper_ready": True, "story_count": 7, "headlines": [{"title": f"H{i}"} for i in range(5)]},
        ("newscast", "search_news"): {"stories": [{"title": "Mars"}]},
        ("notify", "get_weather"): {"configured": True, "place": "Copenhagen", "now": {"temp": 9, "conditions": "light rain"},
                                    "today": {"day": "today", "high": 11, "low": 6, "rain_chance_pct": 80}, "next_hours": [], "next_days": []},
    }

    def fake(app_id, tool, user_id, params=None):
        calls.append((app_id, tool, user_id, params or {}))
        return answers[(app_id, tool)]

    monkeypatch.setattr(apps, "call", fake)
    return calls


def _allow(binding, *names):
    for name in names:
        deps.store().set_policy(binding["id"], name, True)
    return deps.store().get(binding["id"])


def test_personal_tools_start_off(binding, app_calls):
    assert registry.invoke(binding, "get_events")["error"] == "not_allowed"
    assert [t.name for t in registry.tools_for(binding)] == ["get_status", "get_weather"]  # not personal: on
    assert app_calls == []


def test_tools_call_the_app_for_the_owner(binding, app_calls):
    b = _allow(binding, "get_events", "search_news")
    assert registry.invoke(b, "get_events", {"days": 3})["ok"]
    assert registry.invoke(b, "search_news", {"query": "Mars"})["payload"]["stories"][0]["title"] == "Mars"
    assert app_calls == [("eventtrakr", "get_events", USER, {"days": 3}), ("newscast", "search_news", USER, {"query": "Mars"})]
    assert registry.invoke(b, "search_news", {})["error"] == "bad_params"  # query is required


def test_no_app_grant_no_tool(binding, app_calls, monkeypatch):
    b = _allow(binding, "get_events")
    monkeypatch.setattr(people, "user_apps", lambda _uid: {"pinboard"})
    result = registry.invoke(b, "get_events")
    assert result["error"] == "no_access" and app_calls == []
    monkeypatch.setattr(people, "user_apps", lambda _uid: None)  # Auth can't be asked: fail closed
    assert registry.invoke(b, "get_events")["error"] == "no_access"
    assert deps.audit().query(decision="DENIED")[0]["decision"] == "DENIED:no_app"


def test_app_down_is_a_tool_failure(binding, monkeypatch):
    b = _allow(binding, "get_notices")

    def down(*_a, **_k):
        raise apps.AppUnavailable("pinboard isn't answering")

    monkeypatch.setattr(apps, "call", down)
    result = registry.invoke(b, "get_notices")
    assert result["error"] == "app_unavailable" and "pinboard" in result["message"]


def test_get_today_only_has_allowed_and_granted_parts(binding, app_calls, monkeypatch):
    b = _allow(binding, "get_today", "get_events", "get_reminders")  # news tool off
    monkeypatch.setattr(people, "user_apps", lambda _uid: {"eventtrakr", "pinboard", "newscast"})
    today = registry.invoke(b, "get_today")["payload"]
    assert set(today) >= {"events", "reminders"} and "news" not in today
    assert [e["title"] for e in today["events"]["events"]] == ["Jazz"]
    assert {r["text"] for r in today["reminders"]["reminders"]} == {"Bins", "Pay"}
    monkeypatch.setattr(people, "user_apps", lambda _uid: {"pinboard"})  # EventTrakr access taken away
    assert "events" not in registry.invoke(b, "get_today")["payload"]


def test_ask_facts_skip_duplicates_and_run_planned_searches(binding, app_calls, monkeypatch):
    b = _allow(binding, "get_today", "get_events", "get_news_briefing", "search_events", "search_news")
    planned = {}

    def plan(_binding, transcript, specs, news=False, events=False):
        planned["specs"] = sorted(s.name for s in specs)
        planned["news"] = news
        return {"search_events": "jazz"}

    monkeypatch.setattr(ask, "_plan_searches", plan)
    facts = ask._facts(b, "anything with jazz this week?")
    assert "get_today" not in facts  # its parts are on, so it would only repeat them
    assert {"get_status", "get_events", "get_news_briefing", "search_events:jazz"} <= set(facts)
    assert planned["specs"] == ["search_events", "search_news"] and planned["news"] is True
    assert ("eventtrakr", "search_events", USER, {"query": "jazz"}) in app_calls


def test_planner_parses_model_json(binding, monkeypatch):
    import stonepi_ai

    class R:
        text = 'Sure: {"search_news": "Mars landing", "search_events": "", "drop_tables": "x"}'

    monkeypatch.setattr(stonepi_ai, "complete", lambda *_a, **_k: R())
    specs = [registry.spec("search_events"), registry.spec("search_news")]
    assert ask._plan_searches(binding, "news about the Mars landing?", specs) == {"search_news": "Mars landing"}


NOON = datetime(2026, 10, 24, 12, 0)


@pytest.fixture
def fresh_mood(tmp_path, monkeypatch):
    from app import settings

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    mood.reset_state()


def test_curious_after_a_fresh_notice(binding, app_calls, fresh_mood):
    assert mood.compute(binding, local_now=NOON)["mood"] != "curious"  # notices tool off
    mood.reset_state()
    b = _allow(binding, "get_notices")
    out = mood.compute({**b, "last_ask_ok": time.time() - 3600}, local_now=NOON)
    assert out["mood"] == "curious" and out["caption"] == "New on the pinboard"
    calls = len(app_calls)
    mood.compute(b, local_now=NOON)
    assert len(app_calls) == calls  # cached


def test_owner_signals_without_grant_are_ignored(binding, app_calls, fresh_mood, monkeypatch):
    b = _allow(binding, "get_notices", "get_events", "get_reminders", "get_news_briefing")
    monkeypatch.setattr(people, "user_apps", lambda _uid: set())
    assert mood.compute(b, local_now=NOON)["mood"] == "content"
    assert app_calls == []


def test_happy_about_an_event_later_today_with_overdue_caption(binding, app_calls, fresh_mood, monkeypatch):
    soon = (datetime.now().astimezone() + timedelta(minutes=2)).replace(second=0, microsecond=0)
    events = {"events": [{"title": "Jazz in the park", "start": soon.isoformat(timespec="minutes")}]}
    real = apps.call
    monkeypatch.setattr(apps, "call", lambda a, t, u, p=None: events if t == "get_events" else real(a, t, u, p))
    b = _allow(binding, "get_events")
    out = mood.compute(b, local_now=NOON)
    assert out["mood"] == "happy" and out["caption"] == f"Jazz in the park at {soon:%H:%M}"
    assert "Jazz in the park" in out["reason"]
    mood.reset_state()
    b = _allow(binding, "get_reminders")
    out = mood.compute(b, local_now=NOON)
    assert out["mood"] == "happy" and out["caption"] == "1 reminder overdue"
    assert out["reason"].endswith("You have 1 overdue reminder")


def test_curious_when_the_paper_just_came_out(binding, app_calls, fresh_mood, monkeypatch):
    just = datetime.now() - timedelta(minutes=10)
    brief = {"paper_ready": True, "publishes_at": f"{just:%H:%M}", "headlines": []}
    real = apps.call
    monkeypatch.setattr(apps, "call", lambda a, t, u, p=None: brief if t == "get_news_briefing" else real(a, t, u, p))
    b = _allow(binding, "get_news_briefing")
    assert mood.compute(b, local_now=NOON)["caption"] == "Today's paper is out"


def test_voice_choice_and_download(tmp_path, monkeypatch):
    from app import settings

    monkeypatch.setattr(voices, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(env, "piper_voice", "/opt/voices/en_GB-alba-medium.onnx")
    assert voices.current_path() == "/opt/voices/en_GB-alba-medium.onnx"
    assert voices.select("nope")[0] is False
    started = []
    monkeypatch.setattr(voices.threading, "Thread", lambda target, args, **_k: type("T", (), {"start": lambda self: started.append(args)})())
    ok, message = voices.select("en_US-ryan-medium")
    assert ok and "Downloading" in message and started == [("en_US-ryan-medium",)]
    assert voices.catalog()["pending"] == "en_US-ryan-medium"
    # Pretend the download finished: files in place → it becomes the voice.
    folder = voices.voices_dir()
    folder.mkdir(parents=True)
    (folder / "en_US-ryan-medium.onnx").write_bytes(b"x")
    (folder / "en_US-ryan-medium.onnx.json").write_text("{}")
    assert voices.select("en_US-ryan-medium") == (True, "Voice changed.")
    assert voices.current_path().endswith("en_US-ryan-medium.onnx")
    assert next(v for v in voices.catalog()["voices"] if v["id"] == "en_US-ryan-medium")["current"]


def test_voice_urls_are_the_pinned_release():
    assert voices._url("en_GB-northern_english_male-medium") == (
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium"
    )


def test_prompt_knows_pebble_and_the_owners_apps(binding, app_calls, fresh_mood, monkeypatch):
    b = _allow(binding, "get_events")
    b = {**b, "battery": "64", "last_path": "away"}
    monkeypatch.setattr(people, "user_apps", lambda _uid: {"eventtrakr", "pricewatch"})
    about = ask._about(b)
    assert "you belong to Adam" in about and "Your mood is" in about and "64%" in about and "away from home" in about
    guide = ask._app_guide(b)
    assert "EventTrakr" in guide and "get_events (on)" in guide and "search_events (off)" in guide
    assert "PriceWatch" in guide and "can't read this app yet" in guide
    assert "Pinboard" not in guide and "NewsCast" not in guide  # apps the owner doesn't have
    system = ask.SYSTEM_PROMPT.format(owner="Adam", now="now", about=about, guide=guide, facts="{}", previous="")
    assert "switch it on in Sidekick" in system


def test_news_by_category_from_the_planner(binding, app_calls, monkeypatch):
    b = _allow(binding, "get_news_briefing")
    seen = {}

    def plan(_binding, transcript, specs, news=False, events=False):
        seen.update(specs=specs, news=news)
        return {"news_category": "sport"}

    monkeypatch.setattr(ask, "_plan_searches", plan)
    facts = ask._facts(b, "what's in the news in sport?")
    assert seen == {"specs": [], "news": True}  # get_events is off  # the planner runs for the briefing alone too
    assert {"get_news_briefing", "get_news_briefing:sport"} <= set(facts)
    assert ("newscast", "get_news_briefing", USER, {"category": "sport"}) in app_calls
    assert ("newscast", "get_news_briefing", USER, {}) in app_calls


def test_planner_offers_news_category_and_sees_the_last_answer(binding, monkeypatch):
    import stonepi_ai

    sent = {}

    class R:
        text = '{"news_category": "sport", "search_news": ""}'

    def complete(*_a, **kw):
        sent.update(kw)
        return R()

    monkeypatch.setattr(stonepi_ai, "complete", complete)
    ask._remember(binding, "what's in the news?", "Twelve stories in World News, Sport and Tech. Which would you like?")
    plan = ask._plan_searches(binding, "sport", [registry.spec("search_news")], news=True)
    assert plan == {"news_category": "sport"}
    assert "news_category" in sent["system"] and "Which would you like?" in sent["prompt"]


def test_follow_up_memory_expires(binding, monkeypatch):
    ask._recent.clear()
    assert ask._previous_text(binding) == ""
    ask._remember(binding, "what's in the news?", "Which category?")
    assert "Which category?" in ask._previous_text(binding)
    real = ask.time.monotonic
    monkeypatch.setattr(ask.time, "monotonic", lambda: real() + ask.FOLLOWUP_S + 1)
    assert ask._previous(binding) is None


def test_news_params_reach_newscast(binding, app_calls):
    b = _allow(binding, "get_news_briefing", "search_news")
    assert registry.invoke(b, "get_news_briefing", {"category": "Sport"})["ok"]
    assert registry.invoke(b, "search_news", {"query": "derby", "category": "sport"})["ok"]
    assert app_calls[-2:] == [
        ("newscast", "get_news_briefing", USER, {"category": "Sport"}),
        ("newscast", "search_news", USER, {"query": "derby", "category": "sport"}),
    ]


def test_events_window_and_kind_from_the_planner(binding, app_calls, monkeypatch):
    b = _allow(binding, "get_events")
    seen = {}

    def plan(_binding, transcript, specs, news=False, events=False):
        seen.update(news=news, events=events)
        return {"events_when": "next_week", "events_category": "community"}

    monkeypatch.setattr(ask, "_plan_searches", plan)
    facts = ask._facts(b, "what community events are on next week?")
    assert seen == {"news": False, "events": True}
    assert {"get_events", "get_events:next_week/community"} <= set(facts)
    assert ("eventtrakr", "get_events", USER, {"days": 7, "when": "next_week", "category": "community"}) in app_calls

    monkeypatch.setattr(ask, "_plan_searches", lambda *_a, **_k: {"events_when": "tonight"})
    assert "get_events:tonight" in ask._facts(b, "what's on tonight?")
    assert ("eventtrakr", "get_events", USER, {"days": 7, "when": "tonight"}) in app_calls

    monkeypatch.setattr(ask, "_plan_searches", lambda *_a, **_k: {"events_category": "music"})
    assert "get_events:music" in ask._facts(b, "any gigs coming up?")
    assert ("eventtrakr", "get_events", USER, {"days": 14, "category": "music"}) in app_calls


def test_planner_offers_event_keys(binding, monkeypatch):
    import stonepi_ai

    sent = {}

    class R:
        text = '{"events_when": "tomorrow", "events_category": "", "news_category": "sport"}'

    monkeypatch.setattr(stonepi_ai, "complete", lambda *_a, **kw: sent.update(kw) or R())
    ask._recent.clear()
    assert ask._plan_searches(binding, "what's on tomorrow?", [], events=True) == {"events_when": "tomorrow"}
    assert "events_when" in sent["system"] and ask.NEWS_CATEGORY_HINT not in sent["system"]


def test_admins_have_every_app_without_grants(monkeypatch):
    monkeypatch.setattr(people, "roster", lambda: {
        "a1": {"id": "a1", "is_admin": True, "permissions": {"newscast": {}}},
        "m1": {"id": "m1", "is_admin": False, "permissions": {"newscast": {}}},
    })
    assert {"newscast", "eventtrakr", "pinboard"} <= people.user_apps("a1")
    assert "auth" not in people.user_apps("a1")  # system apps aren't something to read
    assert people.user_apps("m1") == {"newscast"}
    assert people.user_apps("nobody") == set()


def test_replies_never_stop_mid_sentence():
    long = "Here are tonight's events. " + "Jazz in the park at seven, then a quiz at the pub. " * 30 + "And a late"
    spoken = ask.speakable(long)
    assert len(spoken) <= ask.SPOKEN_MAX_CHARS and spoken.endswith(".") and not spoken.endswith("late.")
    assert ask.speakable("Three stories in Sport. The first is about the der", cut_short=True) == "Three stories in Sport."
    assert ask.speakable("All good.", cut_short=False) == "All good."
    header = ask.header_text("Tonight there's jazz in Fælledparken at seven. " * 10)
    assert len(header) <= ask.HEADER_MAX_BYTES and header.endswith("%E2%80%A6")
    assert "%C3%A6" in header and "%2" not in header.replace("%E2%80%A6", "")[-4:]



def test_weather_comes_from_notify_and_joins_the_day(binding, app_calls, monkeypatch):
    payload = registry.invoke(binding, "get_weather")["payload"]
    assert payload["place"] == "Copenhagen" and ("notify", "get_weather", USER, {}) in app_calls
    monkeypatch.setattr(people, "user_apps", lambda _uid: set())  # no apps at all: weather still counts
    today = registry.invoke(_allow(binding, "get_today"), "get_today")["payload"]
    assert today["weather"] == {"place": "Copenhagen", "now": {"temp": 9, "conditions": "light rain"},
                                "today": {"day": "today", "high": 11, "low": 6, "rain_chance_pct": 80}}
    assert set(today) == {"date", "weekday", "weather"}


def test_weather_switched_off_is_not_read(binding, app_calls):
    deps.store().set_policy(binding["id"], "get_weather", False)
    b = deps.store().get(binding["id"])
    assert registry.invoke(b, "get_weather")["error"] == "not_allowed"
    assert "get_weather" not in ask._facts(b)
    assert not any(c[1] == "get_weather" for c in app_calls)


def test_ask_facts_and_prompt_cover_the_weather(binding, app_calls, fresh_mood, monkeypatch):
    monkeypatch.setattr(people, "user_apps", lambda _uid: set())
    facts = ask._facts(binding)
    assert facts["get_weather"]["now"]["conditions"] == "light rain"
    guide = ask._app_guide(binding)
    assert "get_weather (on)" in guide and "weather at home" in guide
    assert "Weather: answer from the get_weather facts" in ask.SYSTEM_PROMPT
