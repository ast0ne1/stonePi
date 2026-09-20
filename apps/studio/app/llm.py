from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from app.config import BUILD_KIND_IDS, PROMPT_PATH, PROMPTS_DIR, env
from app.workspace import FILE_MAP_RE

_HTTPS_URL = re.compile(r"https://[^\s\"'<>]+", re.IGNORECASE)


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


def _check_https_urls(text: str) -> None:
    for match in _HTTPS_URL.finditer(text or ""):
        url = match.group(0).rstrip(".,)")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"Only https URLs are allowed in generated sites: {url}")


def _kind_prompt(kind: str) -> str:
    kind_id = (kind or "spa").strip().lower()
    if kind_id not in BUILD_KIND_IDS:
        kind_id = "spa"
    path = PROMPTS_DIR / f"kind_{kind_id}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def system_prompt(extra_files: list[str], *, kind: str = "spa", intent: str = "build") -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.is_file() else ""
    kind_block = _kind_prompt(kind)
    listing = "\n".join(f"- {name}" for name in extra_files) or "- (empty project)"
    mode = (intent or "build").strip().lower()
    if mode not in {"clarify", "build"}:
        mode = "build"
    if mode == "clarify":
        mode_block = (
            "Current turn intent: **clarify**.\n"
            "Ask brief clarifying questions or summarise what you understood. "
            "Do not emit a file map or rewrite project files on this turn."
        )
    else:
        mode_block = (
            "Current turn intent: **build**.\n"
            "Implement or update the site now. Emit a complete file map so Studio preview "
            "shows a playable/usable result immediately (publish comes later)."
        )
        if (kind or "").strip().lower() == "game":
            mode_block += (
                "\nThis is a **game** build: meet every requirement in the Game kind guideline — "
                "real loop, collisions/rules, canvas (or equivalent) graphics, keyboard **and** "
                "on-screen controls, sharp on iPhone 13+ and usable on laptop/desktop. "
                "Do not ship a static mock, emoji-only toy, or click-playfield-only control scheme."
            )
    parts = [base.strip()]
    if kind_block:
        parts.append(kind_block)
    parts.append(mode_block)
    parts.append(f"Current project files:\n{listing}")
    return "\n\n".join(parts) + "\n"


def chat(
    messages: list[dict[str, str]],
    project_files: list[str],
    *,
    kind: str = "spa",
    intent: str = "build",
) -> str:
    sys = system_prompt(project_files, kind=kind, intent=intent)
    anthropic = _secret("ANTHROPIC_API_KEY")
    openai = _secret("OPENAI_API_KEY")
    base_url = _secret("STUDIO_LLM_BASE_URL")

    if base_url:
        return _chat_openai_compatible(sys, messages, base_url=base_url, api_key=openai or "local")
    if anthropic:
        return _chat_anthropic(sys, messages, anthropic)
    if openai:
        return _chat_openai_compatible(sys, messages, base_url="https://api.openai.com", api_key=openai)
    raise RuntimeError("No LLM configured. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to Vault.")


def _chat_anthropic(system: str, messages: list[dict[str, str]], api_key: str) -> str:
    payload = {
        "model": env.llm_model,
        "max_tokens": 8192,
        "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in {"user", "assistant"}],
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    parts = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    text = "\n".join(parts).strip()
    _check_https_urls(text)
    return text


def _chat_openai_compatible(system: str, messages: list[dict[str, str]], *, base_url: str, api_key: str) -> str:
    root = (base_url or "https://api.openai.com").rstrip("/")
    if root and urlparse(root).scheme not in {"http", "https"}:
        raise ValueError("STUDIO_LLM_BASE_URL must be http(s).")
    url = f"{root}/v1/chat/completions"
    payload = {
        "model": env.openai_model,
        "messages": [{"role": "system", "content": system}, *messages],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    _check_https_urls(text)
    return text


def assistant_summary(text: str) -> str:
    cleaned = FILE_MAP_RE.sub("", text or "").strip()
    if cleaned:
        return cleaned
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return "Updated project files."
    except json.JSONDecodeError:
        pass
    return text.strip() or "Done."
