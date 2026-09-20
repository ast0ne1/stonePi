"""Editable Studio LLM prompt files (packaged defaults + optional data overrides)."""

from __future__ import annotations

from pathlib import Path

from app.config import BUILD_KIND_IDS, DATA_DIR, PROMPTS_DIR

PROMPT_DEFS = (
    {
        "id": "fileserve_hosting",
        "file": "fileserve_hosting.md",
        "label": "Hosting rules",
        "blurb": "Shared FileServe hosting rules injected on every LLM turn.",
        "icon": "hosting",
    },
    {
        "id": "kind_spa",
        "file": "kind_spa.md",
        "label": "Single Page App",
        "blurb": "Guideline for the SPA build kind.",
        "icon": "spa",
    },
    {
        "id": "kind_guide",
        "file": "kind_guide.md",
        "label": "Interactive Guide",
        "blurb": "Guideline for the Guide build kind.",
        "icon": "guide",
    },
    {
        "id": "kind_game",
        "file": "kind_game.md",
        "label": "Game",
        "blurb": "Guideline for the Game build kind.",
        "icon": "game",
    },
)

_PROMPT_BY_ID = {item["id"]: item for item in PROMPT_DEFS}
_MAX_CHARS = 120_000

OVERRIDES_DIR = DATA_DIR / "prompts"


def _packaged_path(filename: str) -> Path:
    return PROMPTS_DIR / filename


def _override_path(filename: str) -> Path:
    return OVERRIDES_DIR / filename


def get_prompt_def(prompt_id: str) -> dict | None:
    return _PROMPT_BY_ID.get((prompt_id or "").strip())


def normalize_prompt_id(prompt_id: str | None) -> str:
    key = (prompt_id or "").strip()
    if key in _PROMPT_BY_ID:
        return key
    return PROMPT_DEFS[0]["id"]


def list_prompts() -> list[dict]:
    rows = []
    for item in PROMPT_DEFS:
        override = _override_path(item["file"])
        rows.append(
            {
                **item,
                "customized": override.is_file(),
            }
        )
    return rows


def read_prompt(prompt_id: str) -> str:
    item = get_prompt_def(prompt_id)
    if not item:
        return ""
    override = _override_path(item["file"])
    if override.is_file():
        return override.read_text(encoding="utf-8")
    packaged = _packaged_path(item["file"])
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    return ""


def is_customized(prompt_id: str) -> bool:
    item = get_prompt_def(prompt_id)
    if not item:
        return False
    return _override_path(item["file"]).is_file()


def write_prompt(prompt_id: str, text: str) -> None:
    item = get_prompt_def(prompt_id)
    if not item:
        raise ValueError("Unknown prompt")
    body = text if text is not None else ""
    if len(body) > _MAX_CHARS:
        raise ValueError(f"Prompt is too long (max {_MAX_CHARS:,} characters)")
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    path = _override_path(item["file"])
    path.write_text(body.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def reset_prompt(prompt_id: str) -> None:
    item = get_prompt_def(prompt_id)
    if not item:
        raise ValueError("Unknown prompt")
    path = _override_path(item["file"])
    if path.is_file():
        path.unlink()


def kind_prompt_text(kind: str) -> str:
    kind_id = (kind or "spa").strip().lower()
    if kind_id not in BUILD_KIND_IDS:
        kind_id = "spa"
    return read_prompt(f"kind_{kind_id}").strip()


def hosting_prompt_text() -> str:
    return read_prompt("fileserve_hosting").strip()
