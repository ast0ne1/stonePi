"""A fake platform AI for tests: one provider set up and a scripted model behind httpx."""

from __future__ import annotations

import json

import httpx


def platform_ai(monkeypatch, reply, *, provider: str = "openai", model: str = "gpt-4.1-mini", free: bool = False):
    """Set up ``provider/model`` as the only model in every tier, answering with ``reply``.

    ``reply`` is a string, or a callable (prompt text, system text) -> str | httpx.Response.
    Returns the list of request bodies sent.
    """
    import stonepi_ai.client as client_mod
    from stonepi_ai import config as ai_config

    monkeypatch.setattr(ai_config, "secret_for", lambda pid: "test-key" if pid == provider else "")
    cfg = ai_config.default_config()
    model_id = f"{provider}/{model}"
    if model_id not in {m["id"] for m in cfg["models"]}:
        cfg["models"].append(ai_config.model_entry(provider, model))
    cfg["providers"]["gemini"]["free_tier"] = free
    if provider == "ollama":
        cfg["providers"]["ollama"]["base_url"] = "http://ollama.test:11434"
    cfg["tiers"] = {"cheap": [model_id], "standard": [model_id], "best": [model_id]}
    ai_config.save(cfg)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        sent.append(body)
        messages = body.get("messages") or []
        system = next((m["content"] for m in messages if m["role"] == "system"), body.get("system", ""))
        prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
        out = reply(prompt, system) if callable(reply) else reply
        if isinstance(out, httpx.Response):
            return out
        return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}]})

    monkeypatch.setattr(client_mod, "_transport", httpx.MockTransport(handler))
    return sent


def use_ai(db, *, summaries: bool = True, translate: bool = False, importance: bool = False) -> None:
    from app.services import settings

    settings.set_value(db, "ai_enabled", "1")
    settings.set_value(db, "ai_summaries", "ai" if summaries else "device")
    settings.set_value(db, "translate_provider", "llm" if translate else "google")
    settings.set_value(db, "ai_importance", "ai" if importance else "rules")
    db.commit()
