"""The platform AI config: models, Free/Paid flags, tier order and per-function overrides.

Dashboard → Settings → AI writes ``ai-config.json`` in the Vault folder; apps read it here
(no HTTP to Dashboard, so AI keeps working while Dashboard is down). Keys never live in
this file: they stay in the Vault and are read with ``get_secret``.

    {
      "providers": {"gemini": {"free_tier": true}, "ollama": {"base_url": "http://…:11434"}},
      "models": [{"id": "gemini/gemini-3.5-flash-lite", "provider": "gemini",
                  "model": "gemini-3.5-flash-lite", "label": "…", "free": null}],
      "tiers": {"cheap": ["gemini/…", …], "standard": […], "best": […]},
      "functions": {"newscast.summary": {"tier": "cheap"}, "studio.game": {"models": […]}}
    }

With no file yet, the suggested models and DEFAULT_TIERS apply, so an app works as soon
as a key is in the Vault.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import catalog
from ._files import ai_root, locked, read_json, write_json

CONFIG_NAME = "ai-config.json"
# 2: Gemini 2.5 presets replaced by 3.x (2.5 is closed to new keys).
CONFIG_VERSION = 2
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$")


def config_path() -> Path:
    return ai_root() / CONFIG_NAME


def exists() -> bool:
    return config_path().is_file()


def clean_model_name(value: str) -> str:
    model = (value or "").strip()
    if not _MODEL_RE.match(model):
        raise ValueError("Model IDs use letters, numbers, dots, dashes, colons or slashes (max 120 characters).")
    return model


def model_entry(provider_id: str, model: str, *, label: str = "", free: bool | None = None) -> dict[str, Any]:
    prov = catalog.provider(provider_id)
    if prov is None:
        raise ValueError(f"Unknown provider: {provider_id}")
    model = clean_model_name(model)
    known = catalog.preset(f"{prov.id}/{model}")
    return {
        "id": f"{prov.id}/{model}",
        "provider": prov.id,
        "model": model,
        "label": (label or "").strip()[:80] or (known.label if known else model),
        "free": free if prov.free is None else None,
    }


def default_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "providers": {"gemini": {"free_tier": None}, "ollama": {"base_url": ""}},
        "models": [model_entry(p.provider, p.model) for p in catalog.PRESETS],
        "tiers": {tier: list(catalog.DEFAULT_TIERS.get(tier, ())) for tier in catalog.TIERS},
        "functions": {},
    }


def normalize(data: dict[str, Any] | None) -> dict[str, Any]:
    """A config with every section present and unknown or broken entries dropped."""
    base = default_config()
    if not isinstance(data, dict):
        return base
    data = _migrate(data)
    providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    gemini = providers.get("gemini") if isinstance(providers.get("gemini"), dict) else {}
    ollama = providers.get("ollama") if isinstance(providers.get("ollama"), dict) else {}
    free_tier = gemini.get("free_tier")
    out_providers = {
        "gemini": {"free_tier": free_tier if isinstance(free_tier, bool) else None},
        "ollama": {"base_url": normalize_base_url(str(ollama.get("base_url") or ""))},
    }
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_models = data.get("models") if isinstance(data.get("models"), list) else base["models"]
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        try:
            free = item.get("free")
            entry = model_entry(
                str(item.get("provider") or ""),
                str(item.get("model") or ""),
                label=str(item.get("label") or ""),
                free=free if isinstance(free, bool) else None,
            )
        except ValueError:
            continue
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        models.append(entry)
    tiers_raw = data.get("tiers") if isinstance(data.get("tiers"), dict) else base["tiers"]
    tiers = {tier: _ids(tiers_raw.get(tier), seen) for tier in catalog.TIERS}
    functions: dict[str, dict[str, Any]] = {}
    raw_functions = data.get("functions") if isinstance(data.get("functions"), dict) else {}
    for fid, item in raw_functions.items():
        if not isinstance(item, dict) or "." not in str(fid):
            continue
        row: dict[str, Any] = {}
        if item.get("tier") in catalog.TIERS:
            row["tier"] = item["tier"]
        if isinstance(item.get("models"), list):
            row["models"] = _ids(item["models"], seen)
        if row:
            functions[str(fid)] = row
    out = {"version": CONFIG_VERSION, "providers": out_providers, "models": models, "tiers": tiers, "functions": functions}
    if data.get("updated_at"):
        out["updated_at"] = str(data["updated_at"])
    return out


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Bring an older saved config up to CONFIG_VERSION (in memory; the next save keeps it)."""
    try:
        version = int(data.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= 2:
        return data
    renames = {old: new for old, (new, _label) in catalog.MODEL_RENAMES_V2.items()}
    old_labels = {old: label for old, (_new, label) in catalog.MODEL_RENAMES_V2.items()}
    data = dict(data)
    models = []
    for item in data.get("models") if isinstance(data.get("models"), list) else []:
        if isinstance(item, dict):
            old_id = f"{item.get('provider')}/{item.get('model')}"
            if old_id in renames:
                new_id = renames[old_id]
                label = item.get("label") or ""
                # A label still the old preset name follows the new model; a custom one stays.
                item = {**item, "model": new_id.split("/", 1)[1], "label": "" if label == old_labels[old_id] else label}
        models.append(item)
    if "models" in data:
        data["models"] = models

    def swap(ids: Any) -> Any:
        if not isinstance(ids, list):
            return ids
        out: list[Any] = []
        for value in ids:
            value = renames.get(value, value)
            if value not in out:
                out.append(value)
        return out

    if isinstance(data.get("tiers"), dict):
        data["tiers"] = {tier: swap(ids) for tier, ids in data["tiers"].items()}
    if isinstance(data.get("functions"), dict):
        data["functions"] = {
            fid: ({**row, "models": swap(row["models"])} if isinstance(row, dict) and "models" in row else row)
            for fid, row in data["functions"].items()
        }
    return data


def _ids(values: Any, known: set[str]) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list | tuple) else []:
        value = str(value)
        if value in known and value not in out:
            out.append(value)
    return out


def normalize_base_url(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[:-3].rstrip("/")
    if raw and not raw.startswith(("http://", "https://")):
        raise ValueError("The Ollama URL must start with http:// or https://")
    return raw


def load() -> dict[str, Any]:
    return normalize(read_json(config_path()))


def save(data: dict[str, Any]) -> dict[str, Any]:
    clean = normalize(data)
    clean["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = config_path()
    with locked(path):
        write_json(path, clean)
    return clean


# -- reading the config -----------------------------------------------------------------


def secret_for(provider_id: str) -> str:
    prov = catalog.provider(provider_id)
    if prov is None or not prov.secret:
        return ""
    try:
        from stonepi_vault import get_secret

        return (get_secret(prov.secret, env_name=prov.secret, default="") or "").strip()
    except Exception:
        import os

        return (os.environ.get(prov.secret) or "").strip()


def base_url_for(provider_id: str, cfg: dict[str, Any]) -> str:
    prov = catalog.provider(provider_id)
    if prov is None:
        return ""
    if prov.id == "ollama":
        root = cfg["providers"]["ollama"]["base_url"]
        return f"{root}/v1" if root else ""
    return prov.base_url


def provider_ready(provider_id: str, cfg: dict[str, Any] | None = None) -> bool:
    """Set up: its key is in the Vault (Ollama: its URL is set)."""
    cfg = cfg or load()
    prov = catalog.provider(provider_id)
    if prov is None:
        return False
    if prov.id == "ollama":
        return bool(cfg["providers"]["ollama"]["base_url"])
    return bool(secret_for(prov.id))


def is_free(entry: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Free belongs to the key + model: Ollama always, OpenAI/Anthropic never, Gemini as flagged."""
    prov = catalog.provider(entry.get("provider", ""))
    if prov is None:
        return False
    if prov.free is not None:
        return prov.free
    if isinstance(entry.get("free"), bool):
        return entry["free"]
    return cfg["providers"]["gemini"]["free_tier"] is True


def is_small(entry: dict[str, Any]) -> bool:
    prov = catalog.provider(entry.get("provider", ""))
    return bool(prov and prov.local)


def entry_by_id(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in cfg["models"]}


def function_tier(function_id: str, cfg: dict[str, Any] | None = None, *, suggested: str | None = None) -> str:
    cfg = cfg or load()
    row = cfg["functions"].get(function_id) or {}
    if row.get("tier") in catalog.TIERS:
        return row["tier"]
    known = catalog.function(function_id)
    if known is not None:
        return known.tier
    return suggested if suggested in catalog.TIERS else "standard"


def is_overridden(function_id: str, cfg: dict[str, Any]) -> bool:
    return "models" in (cfg["functions"].get(function_id) or {})


def ranked_ids(function_id: str, cfg: dict[str, Any], *, suggested: str | None = None) -> list[str]:
    """The function's order: its own list, else its tier (an empty tier borrows the nearest one)."""
    row = cfg["functions"].get(function_id) or {}
    if "models" in row:
        return list(row["models"])
    tier = function_tier(function_id, cfg, suggested=suggested)
    index = catalog.TIERS.index(tier)
    # Nearest first, preferring the tier above (a better model) over the one below.
    order = sorted(catalog.TIERS, key=lambda t: (abs(catalog.TIERS.index(t) - index), -catalog.TIERS.index(t)))
    for candidate in order:
        if cfg["tiers"].get(candidate):
            return list(cfg["tiers"][candidate])
    return []


def route(
    function_id: str, cfg: dict[str, Any] | None = None, *, suggested: str | None = None, free_only: bool = False
) -> list[dict[str, Any]]:
    """Models to try for a function, in order, whose provider is set up (health not checked)."""
    cfg = cfg or load()
    entries = entry_by_id(cfg)
    ready: dict[str, bool] = {}
    out: list[dict[str, Any]] = []
    for model_id in ranked_ids(function_id, cfg, suggested=suggested):
        entry = entries.get(model_id)
        if entry is None:
            continue
        pid = entry["provider"]
        if pid not in ready:
            ready[pid] = provider_ready(pid, cfg)
        if not ready[pid]:
            continue
        if free_only and not is_free(entry, cfg):
            continue
        out.append(entry)
    return out


def any_ready(cfg: dict[str, Any] | None = None) -> bool:
    """Some provider is set up and at least one of its models is ranked somewhere."""
    cfg = cfg or load()
    ranked = {mid for ids in cfg["tiers"].values() for mid in ids}
    ranked |= {mid for row in cfg["functions"].values() for mid in row.get("models", [])}
    entries = entry_by_id(cfg)
    return any(provider_ready(entries[mid]["provider"], cfg) for mid in ranked if mid in entries)


def warnings(cfg: dict[str, Any] | None = None) -> list[str]:
    """Free or small local models in the best tier (or a best-tier function's own list)."""
    cfg = cfg or load()
    entries = entry_by_id(cfg)
    out: list[str] = []

    def weak(ids: list[str]) -> list[str]:
        return [entries[mid]["label"] for mid in ids if mid in entries and (is_free(entries[mid], cfg) or is_small(entries[mid]))]

    names = weak(cfg["tiers"]["best"])
    if names:
        out.append(
            f"{', '.join(names)} {'is' if len(names) == 1 else 'are'} in the Best tier. Free and small local "
            "models often cut long output short or write it less well (Studio games use Best)."
        )
    for fid, row in cfg["functions"].items():
        if "models" not in row or function_tier(fid, cfg) != "best":
            continue
        names = weak(row["models"])
        if names:
            fn = catalog.function(fid)
            out.append(f"{', '.join(names)} in {fn.label if fn else fid}'s own list: a Best-tier job.")
    return out
