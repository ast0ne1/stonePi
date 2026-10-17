from __future__ import annotations

import json

import httpx
import pytest

import stonepi_ai.client as client_mod
from stonepi_ai import AIUnavailable, complete, config, health, stream
from stonepi_ai.client import candidates


@pytest.fixture(autouse=True)
def ai_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STONEPI_AI_DIR", str(tmp_path))
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "secret_for", lambda pid: _KEYS.get(pid, ""))
    _KEYS.clear()
    yield tmp_path
    client_mod._transport = None


_KEYS: dict[str, str] = {}


def _transport(handler):
    calls: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append({"url": str(request.url), "body": body, "headers": dict(request.headers)})
        return handler(request, body)

    client_mod._transport = httpx.MockTransport(wrapped)
    return calls


def _openai_reply(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


def _setup(tiers, *, keys=("gemini", "openai"), free_tier=True, functions=None):
    for pid in keys:
        _KEYS[pid] = f"key-{pid}"
    cfg = config.default_config()
    cfg["providers"]["gemini"]["free_tier"] = free_tier
    cfg["tiers"] = tiers
    cfg["functions"] = functions or {}
    return config.save(cfg)


def test_defaults_without_a_file_use_only_set_up_providers():
    assert not config.exists()
    assert config.route("newscast.summary") == []
    _KEYS["anthropic"] = "k"
    assert [m["id"] for m in config.route("newscast.summary")] == ["anthropic/claude-haiku-4-5"]
    assert [m["id"] for m in config.route("studio.game")] == ["anthropic/claude-sonnet-5"]
    assert config.any_ready()


def test_free_flag_belongs_to_provider_and_key():
    cfg = config.default_config()
    entries = config.entry_by_id(cfg)
    assert config.is_free(entries["openai/gpt-4.1-mini"], cfg) is False
    assert config.is_free(entries["gemini/gemini-3.5-flash-lite"], cfg) is False  # not answered yet
    cfg["providers"]["gemini"]["free_tier"] = True
    assert config.is_free(entries["gemini/gemini-3.5-flash-lite"], cfg) is True
    entries["gemini/gemini-3.1-pro-preview"]["free"] = False
    assert config.is_free(entries["gemini/gemini-3.1-pro-preview"], cfg) is False
    ollama = config.model_entry("ollama", "llama3.2:3b")
    assert config.is_free(ollama, cfg) and config.is_small(ollama)


def test_override_and_tier_move_and_empty_tier_borrows():
    cfg = _setup(
        {"cheap": ["gemini/gemini-3.5-flash-lite"], "standard": [], "best": ["openai/gpt-4.1"]},
        functions={"newscast.importance": {"models": ["openai/gpt-4o-mini"]}, "newscast.translate": {"tier": "best"}},
    )
    assert [m["id"] for m in config.route("newscast.importance", cfg)] == ["openai/gpt-4o-mini"]
    assert [m["id"] for m in config.route("newscast.translate", cfg)] == ["openai/gpt-4.1"]
    # studio.spa is standard (empty): borrows best first.
    assert [m["id"] for m in config.route("studio.spa", cfg)] == ["openai/gpt-4.1"]
    # Unknown function runs at the tier the app suggests.
    assert [m["id"] for m in config.route("newapp.thing", cfg, suggested="cheap")] == ["gemini/gemini-3.5-flash-lite"]
    assert [m["id"] for m in config.route("newscast.summary", cfg, free_only=True)] == ["gemini/gemini-3.5-flash-lite"]


def test_warning_for_free_or_local_model_in_best():
    cfg = config.default_config()
    cfg["models"].append(config.model_entry("ollama", "llama3.2:3b"))
    cfg = config.normalize(cfg)
    cfg["tiers"]["best"] = ["ollama/llama3.2:3b", "openai/gpt-4.1"]
    warnings = config.warnings(cfg)
    assert warnings and "llama3.2:3b" in warnings[0]
    cfg["tiers"]["best"] = ["openai/gpt-4.1"]
    assert config.warnings(cfg) == []


def test_complete_falls_over_on_429_and_cools_shared_model():
    _setup({"cheap": ["gemini/gemini-3.5-flash-lite", "openai/gpt-4.1-mini"], "standard": [], "best": []})

    def handler(request, body):
        if "generativelanguage" in str(request.url):
            return httpx.Response(429, json={"error": {"message": "Resource has been exhausted (e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}})
        return _openai_reply("A summary.")

    calls = _transport(handler)
    result = complete("newscast.summary", system="s", prompt="p", max_tokens=100, alert=False)
    assert result.text == "A summary." and result.model_id == "openai/gpt-4.1-mini" and not result.free
    assert len(calls) == 2
    # Gemini 3 Flash can't stop thinking: least effort, and room for it under a short cap.
    assert calls[0]["body"]["reasoning_effort"] == "low" and calls[0]["body"]["max_tokens"] == 1024
    assert calls[1]["headers"]["authorization"] == "Bearer key-openai"
    assert health.cooling_until("gemini/gemini-3.5-flash-lite") is not None
    # Another app sees the cooling model and skips it.
    assert [m["id"] for m in candidates("newscast.translate")] == ["openai/gpt-4.1-mini"]
    line = health.status_line({"id": "gemini/gemini-3.5-flash-lite", "label": "Flash-Lite"})
    assert line["state"] == "cooling" and "rate limited" in line["text"]


def test_429_kinds_and_retry_delay():
    from datetime import datetime, timezone

    _setup({"cheap": ["gemini/gemini-3.5-flash-lite", "openai/gpt-4.1-mini"], "standard": [], "best": []})
    minute = "You exceeded your current quota, please check your plan and billing details. Please retry in 34.5s."
    _transport(lambda request, body: httpx.Response(429, json=[{"error": {"message": minute, "status": "RESOURCE_EXHAUSTED"}}])
               if "generativelanguage" in str(request.url) else _openai_reply("ok"))
    complete("newscast.summary", prompt="p", alert=False)
    row = health.snapshot()["models"]["gemini/gemini-3.5-flash-lite"]
    assert row["kind"] == "rate_limit"  # per-minute, not the 60-minute quota cool-off
    left = (datetime.fromisoformat(row["cooling_until"]) - datetime.now(timezone.utc)).total_seconds()
    assert 20 < left < 60
    health.clear()
    daily = "Quota exceeded for metric: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    _transport(lambda request, body: httpx.Response(429, json={"error": {"message": daily}})
               if "generativelanguage" in str(request.url) else _openai_reply("ok"))
    complete("newscast.summary", prompt="p", alert=False)
    assert health.snapshot()["models"]["gemini/gemini-3.5-flash-lite"]["kind"] == "quota"


def test_healthy_successes_do_not_rewrite_the_file():
    health.mark_ok("openai/gpt-4.1-mini")
    path = health.health_path()
    before = path.stat().st_mtime_ns
    path.write_text(path.read_text())  # same content; mtime marker
    marker = path.stat().st_mtime_ns
    assert not health.mark_ok("openai/gpt-4.1-mini")
    assert path.stat().st_mtime_ns == marker
    health.mark_failed("openai/gpt-4.1-mini", "outage", "down")
    assert health.mark_ok("openai/gpt-4.1-mini")  # recovery is always written
    assert health.cooling_until("openai/gpt-4.1-mini") is None
    assert before


def test_complete_raises_when_all_fail_and_alerts_once(monkeypatch):
    _setup({"cheap": ["openai/gpt-4.1-mini"], "standard": [], "best": []})
    _transport(lambda request, body: httpx.Response(503, json={"error": {"message": "down"}}))
    sent = []
    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda env: sent.append(env) or True)
    with pytest.raises(AIUnavailable) as err:
        complete("newscast.summary", prompt="p")
    assert err.value.reason == "all_failed"
    with pytest.raises(AIUnavailable) as err:
        complete("newscast.summary", prompt="p")
    assert err.value.reason == "no_models"  # cooling now
    assert len(sent) == 1 and sent[0].id == "system.ai_unavailable"


def test_stop_on_rate_limit_does_not_move_down_the_order():
    _setup({"cheap": ["gemini/gemini-3.5-flash-lite", "gemini/gemini-3.8-flash"], "standard": [], "best": []}, keys=("gemini",))
    calls = _transport(lambda request, body: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(AIUnavailable) as err:
        complete("newscast.summary", prompt="p", free_only=True, stop_on_rate_limit=True, alert=False)
    assert err.value.reason == "rate_limited" and len(calls) == 1


def test_anthropic_request_shape_and_reasoning_openai_params():
    _setup({"cheap": ["anthropic/claude-haiku-4-5"], "standard": ["openai/gpt-5-mini"], "best": []}, keys=("anthropic", "openai"))

    def handler(request, body):
        if "anthropic" in str(request.url):
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Hi"}], "stop_reason": "end_turn"})
        return _openai_reply("Yo")

    calls = _transport(handler)
    assert complete("newscast.summary", system="sys", prompt="p", max_tokens=50, temperature=0.2).text == "Hi"
    assert calls[0]["url"].endswith("/v1/messages")
    assert calls[0]["body"]["system"] == "sys" and calls[0]["body"]["max_tokens"] == 50
    assert calls[0]["headers"]["x-api-key"] == "key-anthropic"
    complete("studio.spa", prompt="p", max_tokens=50, temperature=0.2)
    assert calls[1]["body"]["max_completion_tokens"] == 50
    assert "temperature" not in calls[1]["body"] and "max_tokens" not in calls[1]["body"]


def test_requests_count_toward_llm_quota(ai_dir):
    from stonepi_vault.quota import get_quota

    _setup({"cheap": ["openai/gpt-4.1-mini"], "standard": [], "best": []})
    _transport(lambda request, body: _openai_reply("ok"))
    complete("newscast.summary", prompt="p")
    status = get_quota("llm", root=ai_dir).status()
    assert status["limit"] == 20000 and status["by_use"] == {"newscast/summary": 1}
    get_quota("llm", root=ai_dir).set_limit(1)
    with pytest.raises(AIUnavailable) as err:
        complete("newscast.summary", prompt="p", alert=False)
    assert err.value.reason == "quota"


def test_stream_falls_over_before_first_chunk():
    _setup({"cheap": [], "standard": [], "best": ["anthropic/claude-sonnet-5", "openai/gpt-4.1"]}, keys=("anthropic", "openai"))

    def handler(request, body):
        if "anthropic" in str(request.url):
            return httpx.Response(401, json={"error": {"type": "authentication_error", "message": "bad key"}})
        sse = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"length"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    _transport(handler)
    out = list(stream("studio.game", system="s", messages=[{"role": "user", "content": "go"}]))
    assert out == [("model", "GPT-4.1"), ("text", "Hel"), ("text", "lo"), ("stop", "max_tokens")]
    assert health.snapshot()["models"]["anthropic/claude-sonnet-5"]["kind"] == "auth"


def test_save_drops_unknown_ids_and_keeps_ollama_url():
    cfg = config.default_config()
    cfg["providers"]["ollama"]["base_url"] = "http://pi.local:11434/v1/"
    cfg["tiers"]["cheap"] = ["openai/gpt-4.1-mini", "nope/x", "openai/gpt-4.1-mini"]
    cfg["functions"] = {"studio.game": {"models": ["openai/gpt-4.1", "ghost/model"]}, "bad": {"tier": "cheap"}}
    saved = config.save(cfg)
    assert saved["providers"]["ollama"]["base_url"] == "http://pi.local:11434"
    assert saved["tiers"]["cheap"] == ["openai/gpt-4.1-mini"]
    assert saved["functions"] == {"studio.game": {"models": ["openai/gpt-4.1"]}}
    assert config.load()["functions"] == saved["functions"]
    with pytest.raises(ValueError):
        config.normalize_base_url("pi.local:11434")


def test_saved_config_moves_off_gemini_25_once(ai_dir):
    import json

    old = {
        "providers": {"gemini": {"free_tier": True}},
        "models": [
            {"id": "gemini/gemini-2.5-flash-lite", "provider": "gemini", "model": "gemini-2.5-flash-lite",
             "label": "Gemini 2.5 Flash-Lite", "free": None},
            {"id": "gemini/gemini-2.5-pro", "provider": "gemini", "model": "gemini-2.5-pro", "label": "My Pro", "free": False},
            {"id": "openai/gpt-4.1", "provider": "openai", "model": "gpt-4.1", "label": "GPT-4.1", "free": None},
        ],
        "tiers": {"cheap": ["gemini/gemini-2.5-flash-lite"], "standard": [], "best": ["gemini/gemini-2.5-pro", "openai/gpt-4.1"]},
        "functions": {"studio.game": {"models": ["gemini/gemini-2.5-pro"]}},
    }
    (ai_dir / "ai-config.json").write_text(json.dumps(old), encoding="utf-8")
    cfg = config.load()
    entries = config.entry_by_id(cfg)
    assert entries["gemini/gemini-3.5-flash-lite"]["label"] == "Gemini 3.5 Flash-Lite"
    assert entries["gemini/gemini-3.1-pro-preview"]["label"] == "My Pro"  # a custom label stays
    assert entries["gemini/gemini-3.1-pro-preview"]["free"] is False
    assert cfg["tiers"]["cheap"] == ["gemini/gemini-3.5-flash-lite"]
    assert cfg["functions"]["studio.game"]["models"] == ["gemini/gemini-3.1-pro-preview"]
    saved = config.save(cfg)
    assert saved["version"] == config.CONFIG_VERSION
    # Once saved at version 2, a 2.5 model the admin adds back on purpose stays.
    saved["models"].append(config.model_entry("gemini", "gemini-2.5-flash"))
    saved["tiers"]["standard"] = ["gemini/gemini-2.5-flash"]
    assert config.save(saved)["tiers"]["standard"] == ["gemini/gemini-2.5-flash"]
    assert config.load()["tiers"]["standard"] == ["gemini/gemini-2.5-flash"]


def test_gemini_25_flash_still_switches_thinking_off():
    cfg = config.default_config()
    cfg["models"].append(config.model_entry("gemini", "gemini-2.5-flash"))
    cfg["tiers"] = {"cheap": ["gemini/gemini-2.5-flash"], "standard": [], "best": []}
    _KEYS["gemini"] = "k"
    config.save(cfg)
    calls = _transport(lambda request, body: _openai_reply("ok"))
    complete("newscast.importance", prompt="p", max_tokens=20)
    assert calls[0]["body"]["reasoning_effort"] == "none" and calls[0]["body"]["max_tokens"] == 20


def test_thinking_level_refused_retries_without_it():
    _setup({"cheap": ["gemini/gemini-3.8-flash"], "standard": [], "best": []}, keys=("gemini",))

    def handler(request, body):
        if "reasoning_effort" in body:
            return httpx.Response(400, json=[{"error": {"code": 400, "status": "INVALID_ARGUMENT",
                "message": "Thinking level MINIMAL is not supported for this model. Please retry with other thinking level."}}])
        return _openai_reply("ready")

    calls = _transport(handler)
    assert complete("newscast.summary", prompt="p", alert=False).text == "ready"
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]["body"]
    assert health.cooling_until("gemini/gemini-3.8-flash") is None  # not the model's fault
