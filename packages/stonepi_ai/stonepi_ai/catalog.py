"""What StonePi knows about AI: providers, suggested models, tiers and every app's AI functions.

Apps declare their functions here with a **suggested tier, never a model** (like the event
catalog in stonepi_contracts). The admin ranks models once per tier on Dashboard →
Settings → AI and can move a function to another tier or give it its own list. A function
missing from this list still works: it runs at the tier the app passes.
"""

from __future__ import annotations

from dataclasses import dataclass

TIERS: tuple[str, ...] = ("cheap", "standard", "best")
TIER_LABELS = {"cheap": "Cheap", "standard": "Standard", "best": "Best"}
TIER_BLURBS = {
    "cheap": "Short, simple jobs that run a lot: summaries, scores, translation.",
    "standard": "Everyday writing where quality matters.",
    "best": "Long, demanding output such as whole games and apps.",
}


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # "openai" (OpenAI-compatible /chat/completions) | "anthropic"
    base_url: str  # blank: the admin sets it (Ollama)
    secret: str  # Vault key holding the API key; blank: none needed
    free: bool | None  # True always Free, False always Paid, None: asked per key (Gemini)
    local: bool = False
    blurb: str = ""


PROVIDERS: tuple[Provider, ...] = (
    Provider("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
             "GEMINI_API_KEY", None,
             blurb="Google AI Studio key. Its free tier needs no billing."),
    Provider("anthropic", "Anthropic (Claude)", "anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", False,
             blurb="Claude models. Paid per use."),
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", False,
             blurb="GPT models. Paid per use."),
    Provider("ollama", "Ollama", "openai", "", "", True, local=True,
             blurb="A model server on your network (or any OpenAI-compatible server). Free, but small models write less well."),
)
PROVIDER_IDS = tuple(p.id for p in PROVIDERS)
_PROVIDERS = {p.id: p for p in PROVIDERS}


def provider(provider_id: str) -> Provider | None:
    return _PROVIDERS.get(str(provider_id or "").strip().lower())


@dataclass(frozen=True)
class Preset:
    provider: str
    model: str
    label: str
    cost: str  # "$" .. "$$$$"; "" for local
    blurb: str = ""

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"


# Suggested models per provider (any other ID the account can use can be added by hand).
PRESETS: tuple[Preset, ...] = (
    # Gemini 2.5 is closed to keys that hadn't used it before (B11): new keys get a 404.
    Preset("gemini", "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "$", "Fastest and cheapest Gemini; free tier on an AI Studio key."),
    Preset("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash", "$", "Better writing than Flash-Lite; smaller free allowance."),
    Preset("gemini", "gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)", "$$$", "Most capable Gemini; preview, very small free allowance."),
    Preset("anthropic", "claude-haiku-4-5", "Claude Haiku 4.5", "$", "Fastest and cheapest Claude. Fine for simple apps."),
    Preset("anthropic", "claude-sonnet-5", "Claude Sonnet 5", "$$", "Strong at writing games and apps, quick, and good value."),
    Preset("anthropic", "claude-opus-5", "Claude Opus 5", "$$$", "More capable for big or tricky builds. Slower, about 2.5× Sonnet's price."),
    Preset("anthropic", "claude-fable-5-1", "Claude Fable 5.1", "$$$$", "Anthropic's most capable model. Slowest and most expensive."),
    Preset("openai", "gpt-4o-mini", "GPT-4o mini", "$", "Cheapest; its short reply limit cuts full games off part-way."),
    Preset("openai", "gpt-4.1-mini", "GPT-4.1 mini", "$", "Fast and cheap. Fine for simple apps and stories."),
    Preset("openai", "gpt-4.1", "GPT-4.1", "$$$", "Reliable at writing games and apps, with room for long builds."),
    Preset("openai", "gpt-5-mini", "GPT-5 mini", "$$", "A cheaper GPT-5; thinks first, so replies start later."),
    Preset("openai", "gpt-5", "GPT-5", "$$$", "Most capable OpenAI model for big builds; the wait is longer."),
)
_PRESETS = {p.id: p for p in PRESETS}


def preset(model_id: str) -> Preset | None:
    return _PRESETS.get(model_id)


# Model IDs a saved config is moved off once (config version 2): the old preset -> its successor.
MODEL_RENAMES_V2 = {
    "gemini/gemini-2.5-flash-lite": ("gemini/gemini-3.5-flash-lite", "Gemini 2.5 Flash-Lite"),
    "gemini/gemini-2.5-flash": ("gemini/gemini-3.8-flash", "Gemini 2.5 Flash"),
    "gemini/gemini-2.5-pro": ("gemini/gemini-3.1-pro-preview", "Gemini 2.5 Pro"),
}


# The order a fresh install starts with; only models whose provider is set up are used.
DEFAULT_TIERS: dict[str, tuple[str, ...]] = {
    "cheap": ("gemini/gemini-3.5-flash-lite", "anthropic/claude-haiku-4-5", "openai/gpt-4.1-mini"),
    "standard": ("gemini/gemini-3.8-flash", "anthropic/claude-sonnet-5", "openai/gpt-4.1"),
    "best": ("anthropic/claude-sonnet-5", "openai/gpt-4.1"),
}


@dataclass(frozen=True)
class Function:
    id: str  # "<app>.<name>"
    app: str
    label: str
    tier: str  # suggested tier
    blurb: str = ""


FUNCTIONS: tuple[Function, ...] = (
    Function("newscast.summary", "newscast", "Summaries", "cheap", "2–4 sentence story summaries."),
    Function("newscast.translate", "newscast", "Translation", "cheap", "Translates foreign-language stories."),
    Function("newscast.importance", "newscast", "Importance scoring", "cheap", "Scores each story 1–5 for the paper."),
    # Added with the podcast (F8): Function("newscast.podcast_rewrite", "newscast", "Podcast rewrite", "standard", ...)
    Function("studio.spa", "studio", "Handy apps", "standard", "Builds small web apps."),
    Function("studio.guide", "studio", "Stories and guides", "cheap", "Builds stories, quizzes and how-tos."),
    Function("studio.game", "studio", "Games", "best", "Builds whole games: long output, quality matters."),
)
_FUNCTIONS = {f.id: f for f in FUNCTIONS}

APP_LABELS = {"newscast": "NewsCast", "studio": "Studio"}


def function(function_id: str) -> Function | None:
    return _FUNCTIONS.get(function_id)


def app_label(app_id: str) -> str:
    return APP_LABELS.get(app_id, app_id.replace("_", " ").title())
