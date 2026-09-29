from __future__ import annotations

import json
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx

from app import model_settings
from app.config import env
from app.prompt_files import hosting_prompt_text, kind_prompt_text
from app.workspace import FILE_MAP_RE

# Streaming keeps long game builds alive: the read timeout is per chunk, not per reply.
_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)

# Owned by code, not the editable prompt files, because workspace.parse_file_blocks
# depends on it exactly.
_BUILD_FORMAT = """## Output format (build turns)

Start with 1–3 short sentences for the user: what you built or changed and how to play/use it.
Then write every file you create or change as a raw block:

<studio-file path="index.html">
<!doctype html>
...complete file contents, exactly as they should be saved...
</studio-file>

<studio-file path="app.js">
...
</studio-file>

- Paths are relative to the site root, forward slashes.
- Write each file **in full** — never "...rest unchanged", diffs, or placeholders. Omit files you did not change.
- Contents are raw text: no JSON, no escaping, no Markdown fences inside the block.
- index.html must reference only files that exist (either already in the project or written in this reply)."""

_CLARIFY_FORMAT = """## Output format (clarify turns)

Reply conversationally in plain prose (short Markdown lists are fine). Keep it brief: confirm what you understood,
ask at most 3 concrete questions, and suggest sensible defaults so the user can simply press **Build**.
Do not write any files or <studio-file> blocks on this turn."""


def _secret(name: str) -> str:
    try:
        from stonepi_vault import get_secret

        value = get_secret(name, env_name=name, default="")
        if value.strip():
            return value.strip()
    except Exception:
        pass
    import os

    return (os.environ.get(name) or "").strip()


def system_prompt(
    extra_files: list[str],
    *,
    kind: str = "spa",
    intent: str = "build",
    sources: dict[str, str] | None = None,
    needs_name: bool = False,
) -> str:
    base = hosting_prompt_text()
    kind_block = kind_prompt_text(kind)
    listing = "\n".join(f"- {name}" for name in extra_files) or "- (empty project)"
    mode = (intent or "build").strip().lower()
    if mode not in {"clarify", "build"}:
        mode = "build"
    if mode == "clarify":
        mode_block = "Current turn intent: **clarify**.\n\n" + _CLARIFY_FORMAT
    else:
        mode_block = (
            "Current turn intent: **build**.\n"
            "Implement or update the site now so the Studio preview shows a working result immediately."
        )
        if (kind or "").strip().lower() == "game":
            mode_block += (
                "\nThis is a **game** build: meet every requirement in the Game kind guideline — "
                "real loop, collisions/rules, canvas (or equivalent) graphics, keyboard **and** "
                "on-screen controls, sharp on iPhone 13+ and usable on laptop/desktop. "
                "Do not ship a static mock, emoji-only toy, or click-playfield-only control scheme."
            )
        mode_block += (
            "\nThe preview runs inside an iframe: the game must start without needing page focus tricks, "
            "must not throw on load, and must size itself from its own viewport."
            "\n\n" + _BUILD_FORMAT
        )
    parts = [base.strip()]
    if kind_block:
        parts.append(kind_block)
    parts.append(mode_block)
    if needs_name:
        parts.append(
            "The project has **no name yet**; the user may not have thought of one. "
            "On clarify turns you can suggest a couple of fun names. On build turns, pick a short, "
            "catchy title that fits (2–4 words), use it in the page heading and put it in `<title>`. "
            "Studio names the project from `<title>`."
        )
    parts.append(f"Current project files:\n{listing}")
    if sources:
        dump = "\n\n".join(
            f'<current-file path="{name}">\n{body}\n</current-file>' for name, body in sources.items()
        )
        parts.append(
            "Current contents of the project's text files (edit these; the placeholder shell can be replaced entirely):\n\n"
            + dump
        )
    return "\n\n".join(parts) + "\n"


def stream_chat(
    messages: list[dict[str, str]],
    project_files: list[str],
    *,
    kind: str = "spa",
    intent: str = "build",
    sources: dict[str, str] | None = None,
    needs_name: bool = False,
) -> Iterator[tuple[str, str]]:
    """Yield ("text", chunk) pieces, then one ("stop", stop_reason)."""
    sys = system_prompt(project_files, kind=kind, intent=intent, sources=sources, needs_name=needs_name)
    provider = active_provider()
    if provider["id"] == "anthropic":
        return _stream_anthropic(sys, messages, provider["key"])
    if provider["id"] in {"openai", "local"}:
        return _stream_openai_compatible(sys, messages, base_url=provider["base_url"], api_key=provider["key"])
    raise RuntimeError("No LLM configured. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to Vault.")


def key_status() -> dict[str, bool]:
    """Which credentials exist in Vault / env (booleans only, safe to render)."""
    return {
        "anthropic": bool(_secret("ANTHROPIC_API_KEY")),
        "openai": bool(_secret("OPENAI_API_KEY")),
        "local": bool(_secret("STUDIO_LLM_BASE_URL")),
    }


def active_provider() -> dict:
    """Which service Studio will call. Includes the key: never render it.

    The admin's choice wins when its key exists; "auto" (or a missing key) falls back to
    a local OpenAI-compatible server, then Anthropic, then OpenAI.
    """
    anthropic = _secret("ANTHROPIC_API_KEY")
    openai = _secret("OPENAI_API_KEY")
    base_url = _secret("STUDIO_LLM_BASE_URL")
    options = {
        "anthropic": {"id": "anthropic", "label": "Anthropic (Claude)", "base_url": "", "key": anthropic,
                      "model": model_settings.anthropic_model()},
        "openai": {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com", "key": openai,
                   "model": model_settings.openai_model()},
    }
    pref = model_settings.provider_pref()
    if pref in options and options[pref]["key"]:
        return options[pref]
    if base_url:
        return {"id": "local", "label": "OpenAI-compatible server", "base_url": base_url, "key": openai or "local",
                "model": model_settings.openai_model()}
    for name in ("anthropic", "openai"):
        if options[name]["key"]:
            return options[name]
    return {"id": "", "label": "Not configured", "base_url": "", "key": "", "model": ""}


def chat(
    messages: list[dict[str, str]],
    project_files: list[str],
    *,
    kind: str = "spa",
    intent: str = "build",
) -> str:
    return "".join(
        chunk for kind_, chunk in stream_chat(messages, project_files, kind=kind, intent=intent) if kind_ == "text"
    ).strip()


def _sse_data(resp: httpx.Response) -> Iterator[str]:
    for line in resp.iter_lines():
        if line.startswith("data:"):
            yield line[5:].strip()


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    resp.read()
    try:
        detail = (resp.json().get("error") or {}).get("message") or resp.text
    except ValueError:
        detail = resp.text
    raise RuntimeError(f"LLM request failed ({resp.status_code}): {str(detail)[:300]}")


def _stream_anthropic(system: str, messages: list[dict[str, str]], api_key: str) -> Iterator[tuple[str, str]]:
    payload = {
        "model": model_settings.anthropic_model(),
        "max_tokens": env.llm_max_tokens,
        "stream": True,
        "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in {"user", "assistant"}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    stop = "end_turn"
    with httpx.Client(timeout=_TIMEOUT) as client:
        with client.stream("POST", "https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
            _raise_for_status(resp)
            for data in _sse_data(resp):
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        yield "text", str(delta.get("text") or "")
                elif etype == "message_delta":
                    stop = (event.get("delta") or {}).get("stop_reason") or stop
                elif etype == "error":
                    raise RuntimeError((event.get("error") or {}).get("message") or "LLM stream error")
    yield "stop", stop


def _stream_openai_compatible(
    system: str, messages: list[dict[str, str]], *, base_url: str, api_key: str
) -> Iterator[tuple[str, str]]:
    root = (base_url or "https://api.openai.com").rstrip("/")
    if root and urlparse(root).scheme not in {"http", "https"}:
        raise ValueError("STUDIO_LLM_BASE_URL must be http(s).")
    payload = {"model": model_settings.openai_model(), "stream": True, "messages": [{"role": "system", "content": system}, *messages]}
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    stop = "end_turn"
    with httpx.Client(timeout=_TIMEOUT) as client:
        with client.stream("POST", f"{root}/v1/chat/completions", headers=headers, json=payload) as resp:
            _raise_for_status(resp)
            for data in _sse_data(resp):
                if data == "[DONE]":
                    break
                try:
                    choice = (json.loads(data).get("choices") or [{}])[0]
                except (json.JSONDecodeError, AttributeError):
                    continue
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield "text", str(text)
                if choice.get("finish_reason") == "length":
                    stop = "max_tokens"
    yield "stop", stop


def assistant_summary(text: str) -> str:
    cleaned = FILE_MAP_RE.sub("", text or "").strip()
    if cleaned:
        return cleaned
    return text.strip() or "Done."
