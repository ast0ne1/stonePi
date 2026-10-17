"""Calling AI for an app function: the admin's model order, falling over, health and quota.

    from stonepi_ai import complete, AIUnavailable

    try:
        result = complete("newscast.summary", system=SYSTEM, prompt=text, max_tokens=220)
    except AIUnavailable:
        ...the app's non-AI fallback...
    result.text, result.label, result.model_id

Each model in the function's order is tried in turn; a 429, quota, key or outage error
puts that model in the shared health state (cooling off) and moves to the next one.
Every request counts toward the shared ``llm`` quota (stonepi_vault.quota), Free models
included, as a safety net for a mislabelled key. When every model fails the caller gets
AIUnavailable and one admin alert goes to Notify (``alert=True``).

OpenAI, Gemini (its OpenAI-compatible endpoint) and Ollama go to ``/chat/completions``;
Anthropic to ``/messages``. Plain httpx, no SDKs.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import catalog, config, health

logger = logging.getLogger("stonepi.ai")

TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
TEST_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)  # Dashboard's Test button
# Streaming: the read timeout is per chunk, so long builds stay alive.
STREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_MIN_TOKENS = 1024  # Gemini 3 Flash always thinks a little; a 20-token cap would leave no answer
ANTHROPIC_DEFAULT_MAX_TOKENS = 8192  # Anthropic requires a cap; OpenAI-style APIs use the model's own

# Tests swap in an httpx.MockTransport.
_transport: httpx.BaseTransport | None = None


class AIUnavailable(Exception):
    """No model could do the job; the app should use its non-AI path (or show the error).

    ``reason``: "no_models" (none set up / all cooling), "all_failed", "quota" (the shared
    LLM quota is used up) or "rate_limited" (``stop_on_rate_limit`` and a 429 came back).
    """

    def __init__(self, message: str, *, reason: str, errors: list[str] | None = None):
        super().__init__(message)
        self.reason = reason
        self.errors = list(errors or [])


@dataclass
class Result:
    text: str
    model_id: str
    provider: str
    model: str
    label: str
    free: bool
    stop_reason: str = "end_turn"
    tried: list[str] = field(default_factory=list)


class _CallError(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind  # health.COOLDOWN_SECONDS kinds, or "request" (this call only)
        self.retry_after = retry_after


# -- routing ---------------------------------------------------------------------------


def candidates(
    function_id: str, *, tier: str | None = None, free_only: bool = False, cfg: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The models this function would try now, in order (set up and not cooling off)."""
    cfg = cfg or config.load()
    state = health.snapshot()
    out = []
    for entry in config.route(function_id, cfg, suggested=tier, free_only=free_only):
        if health.available(entry["id"], state):
            out.append({**entry, "is_free": config.is_free(entry, cfg)})
    return out


def ready(function_id: str | None = None, *, tier: str | None = None, free_only: bool = False) -> bool:
    """Some model could take the job now (no function: any provider set up with a ranked model)."""
    if function_id is None:
        return config.any_ready()
    return bool(candidates(function_id, tier=tier, free_only=free_only))


def describe(function_id: str, *, tier: str | None = None) -> str:
    """The label of the model the function would use first, or ""."""
    found = candidates(function_id, tier=tier)
    return found[0]["label"] if found else ""


# -- calls ----------------------------------------------------------------------------


def complete(
    function_id: str,
    *,
    system: str = "",
    prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
    tier: str | None = None,
    free_only: bool = False,
    stop_on_rate_limit: bool = False,
    alert: bool = True,
    timeout: httpx.Timeout | None = None,
) -> Result:
    """One reply from the first model in the function's order that answers."""
    msgs = _messages(prompt, messages)
    cfg = config.load()
    models = candidates(function_id, tier=tier, free_only=free_only, cfg=cfg)
    if not models:
        _maybe_alert(function_id, alert, "no AI model is set up or every one is cooling off")
        raise AIUnavailable("No AI model is available for this.", reason="no_models")
    errors: list[str] = []
    tried: list[str] = []
    for entry in models:
        _reserve(function_id)
        tried.append(entry["id"])
        try:
            text, stop = _call(entry, cfg, system, msgs, max_tokens, temperature, timeout or TIMEOUT)
        except _CallError as exc:
            errors.append(f"{entry['label']}: {exc}")
            if exc.kind != "request":
                health.mark_failed(entry["id"], exc.kind, str(exc), retry_after=exc.retry_after)
            if stop_on_rate_limit and exc.kind in {"rate_limit", "quota"}:
                raise AIUnavailable(f"{entry['label']} is rate limited.", reason="rate_limited", errors=errors) from exc
            continue
        health.mark_ok(entry["id"], function_id)
        return Result(text, entry["id"], entry["provider"], entry["model"], entry["label"], entry["is_free"], stop, tried)
    _maybe_alert(function_id, alert, "; ".join(errors))
    raise AIUnavailable("Every AI model failed: " + "; ".join(errors), reason="all_failed", errors=errors)


def stream(
    function_id: str,
    *,
    system: str = "",
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    anthropic_max_tokens: int | None = None,
    temperature: float | None = None,
    tier: str | None = None,
) -> Iterator[tuple[str, str]]:
    """Yield ("model", label), then ("text", chunk)…, then one ("stop", stop_reason).

    ``max_tokens`` None lets OpenAI-style models write up to their own limit (a fixed cap
    above a model's limit is an error there); Claude gets ``anthropic_max_tokens``.
    Falls over to the next model only before any text has arrived; an error after that
    is raised (half a reply can't be stitched to another model's).
    """
    cfg = config.load()
    models = candidates(function_id, tier=tier, cfg=cfg)
    if not models:
        raise AIUnavailable("No AI model is set up for this. Ask an admin to add one in Dashboard → Settings → AI.",
                            reason="no_models")
    msgs = _messages(None, messages)
    errors: list[str] = []
    for entry in models:
        _reserve(function_id)
        started = False
        try:
            cap = (anthropic_max_tokens or max_tokens) if entry["provider"] == "anthropic" else max_tokens
            for kind, value in _stream_call(entry, cfg, system, msgs, cap, temperature):
                if not started:
                    started = True
                    yield "model", entry["label"]
                yield kind, value
        except _CallError as exc:
            if exc.kind != "request":
                health.mark_failed(entry["id"], exc.kind, str(exc), retry_after=exc.retry_after)
            if started:
                raise RuntimeError(f"LLM request failed: {exc}") from exc
            errors.append(f"{entry['label']}: {exc}")
            continue
        health.mark_ok(entry["id"], function_id)
        return
    raise AIUnavailable("Every AI model failed: " + "; ".join(errors), reason="all_failed", errors=errors)


def _messages(prompt: str | None, messages: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if messages is None:
        return [{"role": "user", "content": prompt or ""}]
    return [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in {"user", "assistant"}]


def _reserve(function_id: str) -> None:
    try:
        from stonepi_vault.quota import QuotaExceeded, get_quota
    except ImportError:
        return
    use = function_id.replace(".", "/", 1)
    try:
        get_quota("llm", root=_quota_root()).reserve(use, 1)
    except QuotaExceeded as exc:
        raise AIUnavailable(f"The shared AI allowance is used up: {exc}", reason="quota") from exc
    except OSError as exc:  # quota file unwritable: don't block AI over bookkeeping
        logger.warning("llm quota not counted: %s", exc)


def _quota_root():
    from ._files import ai_root

    return ai_root()


def _maybe_alert(function_id: str, alert: bool, detail: str) -> None:
    if not alert:
        return
    try:
        if not health.function_failed(function_id):
            return
        from stonepi_contracts import EventEnvelope, emit_event

        fn = catalog.function(function_id)
        app = fn.app if fn else function_id.split(".", 1)[0]
        what = f"{catalog.app_label(app)} {fn.label.lower() if fn else function_id}"
        emit_event(
            EventEnvelope(
                id="system.ai_unavailable",
                source="system",
                audience="admin",
                title=f"AI unavailable: {what}"[:120],
                summary=f"Every AI model for {what} failed, so it's using its non-AI fallback. {detail}"[:500],
                severity="warning",
                dedupe_key=f"ai:unavailable:{function_id}",
                data={"function": function_id},
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("ai alert not sent", exc_info=True)


# -- providers ------------------------------------------------------------------------


def _client(timeout: httpx.Timeout) -> httpx.Client:
    if _transport is not None:
        return httpx.Client(timeout=timeout, transport=_transport)
    return httpx.Client(timeout=timeout)


def _request(entry: dict[str, Any], cfg: dict[str, Any], system: str, msgs, max_tokens: int | None,
             temperature: float | None, *, stream: bool) -> tuple[str, dict[str, str], dict[str, Any]]:
    prov = catalog.provider(entry["provider"])
    if prov is None:
        raise _CallError("model", f"unknown provider {entry['provider']}")
    base = config.base_url_for(prov.id, cfg).rstrip("/")
    key = config.secret_for(prov.id)
    if not base:
        raise _CallError("auth", "no server URL set")
    if prov.secret and not key:
        raise _CallError("auth", "no API key in the Vault")
    if prov.kind == "anthropic":
        cap = int(max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS)
        payload: dict[str, Any] = {"model": entry["model"], "max_tokens": cap, "messages": msgs}
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        return f"{base}/messages", headers, payload
    payload = {"model": entry["model"], "messages": ([{"role": "system", "content": system}] if system else []) + msgs}
    model = entry["model"].lower()
    reasoning = prov.id == "openai" and (model.startswith(("gpt-5", "o1", "o3", "o4")))
    # Reasoning models take max_completion_tokens and only their default temperature.
    if max_tokens:
        payload["max_completion_tokens" if reasoning else "max_tokens"] = int(max_tokens)
    if temperature is not None and not reasoning:
        payload["temperature"] = temperature
    if prov.id == "gemini" and "flash" in model:
        # Flash models think by default and the thinking counts toward max_tokens. 2.5 can
        # switch it off; 3.x can't, and which levels a model takes varies (3.8 Flash refuses
        # "minimal"), so ask for "low", the one every 3.x Flash lists, and give short caps
        # room for it. A model that refuses even that is retried without (_without_thinking).
        if model.startswith("gemini-2."):
            payload["reasoning_effort"] = "none"
        else:
            payload["reasoning_effort"] = "low"
            if max_tokens:
                payload["max_tokens"] = max(int(max_tokens), GEMINI_MIN_TOKENS)
    if stream:
        payload["stream"] = True
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    return f"{base}/chat/completions", headers, payload


def _without_thinking(exc: _CallError, payload: dict[str, Any]) -> bool:
    """A 400 about the thinking level: drop reasoning_effort (the model's default) and retry."""
    if exc.kind == "request" and "reasoning_effort" in payload and "thinking" in str(exc).lower():
        payload.pop("reasoning_effort")
        return True
    return False


def _call(entry, cfg, system, msgs, max_tokens, temperature, timeout) -> tuple[str, str]:
    url, headers, payload = _request(entry, cfg, system, msgs, max_tokens, temperature, stream=False)
    while True:
        try:
            with _client(timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise _CallError("outage", f"timed out ({exc.__class__.__name__})") from exc
        except httpx.HTTPError as exc:
            raise _CallError("outage", f"no connection ({exc.__class__.__name__})") from exc
        try:
            _raise_for_status(resp)
            break
        except _CallError as exc:
            if not _without_thinking(exc, payload):
                raise
    try:
        data = resp.json()
    except ValueError as exc:
        raise _CallError("outage", "reply was not JSON") from exc
    if entry["provider"] == "anthropic":
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        stop = data.get("stop_reason") or "end_turn"
    else:
        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content")) or ""
        stop = "max_tokens" if choice.get("finish_reason") == "length" else "end_turn"
    text = str(text).strip()
    if not text:
        raise _CallError("request", "empty reply")
    return text, stop


def _stream_call(entry, cfg, system, msgs, max_tokens, temperature) -> Iterator[tuple[str, str]]:
    url, headers, payload = _request(entry, cfg, system, msgs, max_tokens, temperature, stream=True)
    try:
        yield from _stream_once(entry, url, headers, payload)
    except _CallError as exc:
        # Refused before any text arrived (status error), so a retry can't duplicate output.
        if not _without_thinking(exc, payload):
            raise
        yield from _stream_once(entry, url, headers, payload)


def _stream_once(entry, url, headers, payload) -> Iterator[tuple[str, str]]:
    stop = "end_turn"
    try:
        with _client(STREAM_TIMEOUT) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _raise_for_status(resp)
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if entry["provider"] == "anthropic":
                        etype = event.get("type")
                        if etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield "text", str(delta["text"])
                        elif etype == "message_delta":
                            stop = (event.get("delta") or {}).get("stop_reason") or stop
                        elif etype == "error":
                            err = event.get("error") or {}
                            kind = "outage" if err.get("type") == "overloaded_error" else "request"
                            raise _CallError(kind, err.get("message") or "stream error")
                    else:
                        choice = (event.get("choices") or [{}])[0]
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield "text", str(text)
                        if choice.get("finish_reason") == "length":
                            stop = "max_tokens"
    except httpx.TimeoutException as exc:
        raise _CallError("outage", f"timed out ({exc.__class__.__name__})") from exc
    except httpx.HTTPError as exc:
        raise _CallError("outage", f"no connection ({exc.__class__.__name__})") from exc
    yield "stop", stop


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if status < 400:
        return
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(body, list) and body:  # Gemini sometimes wraps the error in a list
            err = (body[0] or {}).get("error")
        detail = (err.get("message") if isinstance(err, dict) else err) or resp.text
        code = str((err or {}).get("code") or (err or {}).get("status") or (err or {}).get("type") or "") if isinstance(err, dict) else ""
    except ValueError:
        detail, code = resp.text, ""
    detail = " ".join(str(detail).split())[:240]
    message = f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
    lowered = f"{code} {detail}".lower()
    retry_after = _retry_after(resp) or _retry_in_message(lowered)
    if status == 429 or "resource_exhausted" in lowered:
        # Gemini's free tier says "quota ... billing" for its per-minute limit too, so only a
        # daily limit or an empty balance counts as a used-up quota (the long cool-off).
        quota = any(word in lowered for word in ("insufficient_quota", "per day", "perday", "daily"))
        raise _CallError("quota" if quota else "rate_limit", message, retry_after)
    if status in {401, 403}:
        raise _CallError("auth", message)
    if status == 404:
        raise _CallError("model", message)
    if status == 402:
        raise _CallError("quota", message)
    if status >= 500:
        raise _CallError("outage", message, retry_after)
    if "thinking" in lowered:
        # "Thinking level X is not supported for this model": our request, not a missing model.
        raise _CallError("request", message)
    if "model" in lowered and any(word in lowered for word in ("not found", "does not exist", "not supported", "invalid model")):
        raise _CallError("model", message)
    raise _CallError("request", message)


def _retry_in_message(text: str) -> float | None:
    """Gemini puts the wait in the message: "Please retry in 34.5s"."""
    match = re.search(r"retry in ([0-9.]+)\s*s", text)
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


# -- admin helpers (Dashboard) --------------------------------------------------------


def list_ollama_models(base_url: str) -> list[str]:
    root = config.normalize_base_url(base_url)
    if not root:
        raise ValueError("Set the Ollama URL first.")
    with _client(httpx.Timeout(5.0)) as client:
        resp = client.get(f"{root}/api/tags")
        resp.raise_for_status()
        data = resp.json()
    names = {str(item.get("name") or item.get("model")) for item in data.get("models") or [] if item.get("name") or item.get("model")}
    return sorted(names, key=str.lower)


def test_model(model_id: str) -> Result:
    """One tiny request to a single model (Dashboard's Test button); updates its health."""
    cfg = config.load()
    entry = config.entry_by_id(cfg).get(model_id)
    if entry is None:
        raise ValueError("Unknown model.")
    _reserve("system.test")
    try:
        text, stop = _call(entry, cfg, "", [{"role": "user", "content": "Reply with the single word: ready"}], 200, None, TEST_TIMEOUT)
    except _CallError as exc:
        if exc.kind != "request":
            health.mark_failed(entry["id"], exc.kind, str(exc), retry_after=exc.retry_after)
        raise AIUnavailable(f"{entry['label']}: {exc}", reason="all_failed", errors=[str(exc)]) from exc
    health.mark_ok(entry["id"])
    return Result(text, entry["id"], entry["provider"], entry["model"], entry["label"], config.is_free(entry, cfg), stop, [entry["id"]])
