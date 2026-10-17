from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from app.config import BUILD_KINDS, DATA_DIR, env
from app.prompt_files import hosting_prompt_text, kind_prompt_text
from app.workspace import FILE_MAP_RE

logger = logging.getLogger("studio.llm")

# Each build kind is its own platform AI function; Dashboard → Settings → AI sets its models.
AI_FUNCTIONS = {"spa": "studio.spa", "guide": "studio.guide", "game": "studio.game"}
LEGACY_SETTINGS = DATA_DIR / "settings.json"

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


def function_for(kind: str) -> str:
    return AI_FUNCTIONS.get((kind or "spa").strip().lower(), "studio.spa")


def stream_chat(
    messages: list[dict[str, str]],
    project_files: list[str],
    *,
    kind: str = "spa",
    intent: str = "build",
    sources: dict[str, str] | None = None,
    needs_name: bool = False,
) -> Iterator[tuple[str, str]]:
    """Yield ("model", label), ("text", chunk) pieces, then one ("stop", stop_reason)."""
    from stonepi_ai import stream

    sys = system_prompt(project_files, kind=kind, intent=intent, sources=sources, needs_name=needs_name)
    return stream(function_for(kind), system=sys, messages=messages, anthropic_max_tokens=env.llm_max_tokens)


def model_overview() -> list[dict]:
    """Per build kind: the models it would try now, in order (labels only, safe to render)."""
    from stonepi_ai import candidates

    rows = []
    for item in BUILD_KINDS:
        try:
            found = candidates(function_for(item["id"]))
        except Exception:  # noqa: BLE001 - Vault folder unreadable
            found = []
        rows.append({"kind": item["id"], "label": item["label"], "models": [m["label"] for m in found]})
    return rows


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


def migrate_legacy_models() -> bool:
    """Move Studio's old admin model choice (settings.json) into the platform AI config, once.

    Studio 0.0.8 and older picked one Anthropic and one OpenAI model plus a preferred
    service. Each build kind gets those two as its own list, in the preferred order, so
    builds keep using the same models until an admin changes them on Settings → AI.
    """
    try:
        data = json.loads(LEGACY_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not ({"provider", "anthropic_model", "openai_model"} & set(data)):
        return False
    try:
        from stonepi_ai import config as ai_config

        if not ai_config.exists():
            cfg = ai_config.default_config()
            known = {m["id"] for m in cfg["models"]}
            ids = []
            for provider, model in (
                ("anthropic", str(data.get("anthropic_model") or env.llm_model)),
                ("openai", str(data.get("openai_model") or env.openai_model)),
            ):
                entry = ai_config.model_entry(provider, model)
                if entry["id"] not in known:
                    cfg["models"].append(entry)
                ids.append(entry["id"])
            if data.get("provider") == "openai":
                ids.reverse()
            for function in AI_FUNCTIONS.values():
                cfg["functions"][function] = {"models": list(ids)}
            ai_config.save(cfg)
        for key in ("provider", "anthropic_model", "openai_model"):
            data.pop(key, None)
        if data:
            LEGACY_SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
        else:
            LEGACY_SETTINGS.unlink()
    except Exception:  # noqa: BLE001 - never block startup over this
        logger.exception("moving Studio's model choice to the platform AI config failed")
        return False
    return True


def assistant_summary(text: str) -> str:
    cleaned = FILE_MAP_RE.sub("", text or "").strip()
    if cleaned:
        return cleaned
    return text.strip() or "Done."
