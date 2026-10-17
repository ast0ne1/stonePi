"""What NewsCast uses AI for (backlog U18b), and what each story got (U1).

The platform holds the providers, models and their order (Dashboard → Settings → AI,
package ``stonepi_ai``); NewsCast only holds what AI is used for:

- **Use AI in NewsCast** (``ai_enabled``): the master switch, off by default, so a key
  added to the Vault for another app never turns AI on here by itself. Greyed out while
  the platform has no provider set up.
- **Summaries** (``ai_summaries``): ``device`` (sumy, default) or ``ai``.
- **Translation** (``translate_provider``): ``google`` (default) or ``llm``.
- **Importance** (``ai_importance``): ``rules`` (``heuristic_importance``, default) or ``ai``.

Sources can only opt out (Summarise off, or translate with Google), never opt in.

Each story keeps a record per task in ``Story.ai_record`` (JSON):
``{"summary": {"by": "ai", "model": "Gemini 2.5 Flash-Lite", "model_id": "gemini/…"}}``,
``{"by": "fallback", "reason": "off" | "unavailable"}`` or ``{"by": "opted_out"}``.
The catch-up (``catchup.py``) redoes only "fallback" ones.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger("newscast.ai")

FN_SUMMARY = "newscast.summary"
FN_TRANSLATE = "newscast.translate"
FN_IMPORTANCE = "newscast.importance"
TASKS = ("summary", "translate", "importance")

SUMMARY_CHOICES = [("device", "On device (default)"), ("ai", "AI")]
IMPORTANCE_CHOICES = [("rules", "Rule-based (default)"), ("ai", "AI")]

BY_AI = "ai"
BY_FALLBACK = "fallback"
BY_OPTED_OUT = "opted_out"


@dataclass(frozen=True)
class AiSettings:
    available: bool  # the platform has a provider set up with a ranked model
    enabled: bool  # master switch on (and available)
    summaries: bool
    translate: bool
    importance: bool

    @property
    def any(self) -> bool:
        return self.summaries or self.translate or self.importance

    def uses(self, task: str) -> bool:
        return {"summary": self.summaries, "translate": self.translate, "importance": self.importance}.get(task, False)


AI_OFF = AiSettings(False, False, False, False, False)


def platform_available() -> bool:
    try:
        import stonepi_ai

        return bool(stonepi_ai.ready())
    except Exception:  # noqa: BLE001 - package missing or Vault folder unreadable
        return False


def ai_settings(db: Session) -> AiSettings:
    from app.services import settings

    available = platform_available()
    enabled = available and settings.get_value(db, "ai_enabled") == "1"
    return AiSettings(
        available=available,
        enabled=enabled,
        summaries=enabled and settings.get_value(db, "ai_summaries") == "ai",
        translate=enabled and settings.translate_provider(db) == "llm",
        importance=enabled and settings.get_value(db, "ai_importance") == "ai",
    )


def first_model(function: str) -> dict[str, Any] | None:
    """The model a task would use now: {label, local}; None when none is available."""
    try:
        from stonepi_ai import candidates

        found = candidates(function)
    except Exception:  # noqa: BLE001
        return None
    if not found:
        return None
    return {"label": found[0]["label"], "local": found[0]["provider"] == "ollama", "free": found[0]["is_free"]}


def complete(function: str, db: Session | None, what: str, **kwargs):
    """One AI reply, or None when every model failed (the error goes on the Status page).

    ``stonepi_ai.AIUnavailable`` with reason "rate_limited" is re-raised when the caller
    asked to stop on a rate limit (the catch-up).
    """
    from stonepi_ai import AIUnavailable
    from stonepi_ai import complete as ai_complete

    from app.services.importance import clear_ai_error, record_ai_error

    try:
        result = ai_complete(function, **kwargs)
    except AIUnavailable as exc:
        if exc.reason == "rate_limited" and kwargs.get("stop_on_rate_limit"):
            raise
        record_ai_error(db, f"{what} used the non-AI fallback: {exc}")
        return None
    clear_ai_error(db)
    return result


# -- per-story record (U1) -------------------------------------------------------------


def by_ai(result) -> dict[str, str]:
    return {"by": BY_AI, "model": result.label, "model_id": result.model_id}


def fell_back(reason: str) -> dict[str, str]:
    return {"by": BY_FALLBACK, "reason": reason}


def opted_out() -> dict[str, str]:
    return {"by": BY_OPTED_OUT}


def load_record(story) -> dict[str, dict[str, str]]:
    raw = story if story is None or isinstance(story, str) else getattr(story, "ai_record", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in TASKS and isinstance(v, dict)} if isinstance(data, dict) else {}


def dump_record(record: dict[str, dict[str, str]]) -> str | None:
    clean = {k: v for k, v in record.items() if k in TASKS and v}
    return json.dumps(clean, separators=(",", ":")) if clean else None


def card_labels(story) -> dict[str, Any]:
    """What the Briefing/Search card says about how the story was made."""
    record = load_record(story)
    summary = record.get("summary") or {}
    translate = record.get("translate") or {}
    importance = record.get("importance") or {}
    out: dict[str, Any] = {
        "summary_ai": summary.get("by") == BY_AI,
        "summary_model": summary.get("model", ""),
        "summary_missed": summary.get("by") == BY_FALLBACK and summary.get("reason") == "unavailable",
        "translated_by": "",
        "translate_model": translate.get("model", ""),
        "importance_model": importance.get("model", "") if importance.get("by") == BY_AI else "",
    }
    if translate.get("by") == BY_AI:
        out["translated_by"] = "ai"
    elif translate:
        out["translated_by"] = "google"
    return out
