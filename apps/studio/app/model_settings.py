"""Admin-chosen LLM model, saved in DATA_DIR so it survives restarts and upgrades."""

from __future__ import annotations

import json
import re

from app.config import DATA_DIR, env

SETTINGS_PATH = DATA_DIR / "settings.json"

# Current Claude models, cheapest/fastest last. IDs are exact API model strings.
MODEL_CHOICES = (
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "blurb": "Recommended. Strong at writing games and apps, quick, and good value.",
        "cost": "$$",
    },
    {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "blurb": "More capable for big or tricky builds. Slower, and about 2.5× Sonnet's price.",
        "cost": "$$$",
    },
    {
        "id": "claude-fable-5-1",
        "label": "Claude Fable 5.1",
        "blurb": "Anthropic's most capable model. The slowest and most expensive (about 5× Sonnet).",
        "cost": "$$$$",
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "blurb": "Fastest and cheapest. Fine for simple apps; games may be less polished.",
        "cost": "$",
    },
)
_CHOICE_IDS = {item["id"] for item in MODEL_CHOICES}

# OpenAI chat models, same shape as MODEL_CHOICES. Any other ID the account can use is
# accepted via "Other model ID".
OPENAI_CHOICES = (
    {
        "id": "gpt-4.1",
        "label": "GPT-4.1",
        "blurb": "Recommended. Reliable at writing games and apps, starts replying quickly, and has room for long builds.",
        "cost": "$$$",
    },
    {
        "id": "gpt-5",
        "label": "GPT-5",
        "blurb": "Most capable for big or tricky builds. Thinks before it answers, so the wait is longer.",
        "cost": "$$$",
    },
    {
        "id": "gpt-5-mini",
        "label": "GPT-5 mini",
        "blurb": "A cheaper GPT-5. Good results for most projects, and still thinks first.",
        "cost": "$$",
    },
    {
        "id": "gpt-4.1-mini",
        "label": "GPT-4.1 mini",
        "blurb": "Fast and cheap. Fine for simple apps and stories; games may be less polished.",
        "cost": "$",
    },
    {
        "id": "gpt-4o-mini",
        "label": "GPT-4o mini",
        "blurb": "Cheapest, but its short reply limit often cuts full games off part-way. Best for small apps.",
        "cost": "$",
    },
)
_OPENAI_IDS = {item["id"] for item in OPENAI_CHOICES}

PROVIDERS = ("auto", "anthropic", "openai")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")


def _load() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def anthropic_model() -> str:
    return str(_load().get("anthropic_model") or env.llm_model)


def openai_model() -> str:
    return str(_load().get("openai_model") or env.openai_model)


def provider_pref() -> str:
    value = str(_load().get("provider") or "auto")
    return value if value in PROVIDERS else "auto"


def is_custom(model_id: str) -> bool:
    return model_id not in _CHOICE_IDS


def is_custom_openai(model_id: str) -> bool:
    return model_id not in _OPENAI_IDS


def clean_model_id(value: str) -> str:
    model = (value or "").strip()
    if not _MODEL_ID_RE.match(model):
        raise ValueError("Model IDs use letters, numbers, dots, dashes, colons or slashes (max 100 characters).")
    return model


def save(*, anthropic: str | None = None, openai: str | None = None, provider: str | None = None) -> None:
    data = _load()
    if provider is not None:
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider.")
        data["provider"] = provider
    if anthropic is not None:
        data["anthropic_model"] = clean_model_id(anthropic)
    if openai is not None:
        data["openai_model"] = clean_model_id(openai)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def reset() -> None:
    data = _load()
    data.pop("anthropic_model", None)
    data.pop("openai_model", None)
    data.pop("provider", None)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
