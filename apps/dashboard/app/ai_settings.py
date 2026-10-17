"""Settings → AI: the platform's AI providers, model tiers and per-function overrides (backlog F13).

Writes stonepi_ai's config file; keys stay in the Vault (the AI group on Settings → Vault).
"""

from __future__ import annotations

import json
from typing import Any

from stonepi_ai import catalog, config, health


def view() -> dict[str, Any]:
    cfg = config.load()
    state = health.snapshot()
    entries = config.entry_by_id(cfg)
    providers = []
    for prov in catalog.PROVIDERS:
        ready = config.provider_ready(prov.id, cfg)
        if prov.free is True:
            billing = "Free"
        elif prov.free is False:
            billing = "Paid"
        else:
            free_tier = cfg["providers"]["gemini"]["free_tier"]
            billing = {True: "Free tier", False: "Paid"}.get(free_tier, "Free or paid?")
        providers.append(
            {
                "id": prov.id,
                "label": prov.label,
                "blurb": prov.blurb,
                "secret": prov.secret,
                "ready": ready,
                "billing": billing,
                "local": prov.local,
                "base_url": cfg["providers"]["ollama"]["base_url"] if prov.id == "ollama" else "",
                "ask_free": prov.free is None and ready and cfg["providers"]["gemini"]["free_tier"] is None,
                "free_tier": cfg["providers"]["gemini"]["free_tier"] if prov.free is None else None,
            }
        )
    ready_by_provider = {p["id"]: p["ready"] for p in providers}
    models = []
    for entry in cfg["models"]:
        known = catalog.preset(entry["id"])
        prov = catalog.provider(entry["provider"])
        models.append(
            {
                **entry,
                "provider_label": prov.label if prov else entry["provider"],
                "is_free": config.is_free(entry, cfg),
                "small": config.is_small(entry),
                "cost": known.cost if known else "",
                "blurb": known.blurb if known else "",
                "ready": ready_by_provider.get(entry["provider"], False),
                "can_flag": bool(prov and prov.free is None),
                "status": health.status_line(entry, state),
            }
        )
    tiers = [
        {"id": tier, "label": catalog.TIER_LABELS[tier], "blurb": catalog.TIER_BLURBS[tier], "models": cfg["tiers"][tier]}
        for tier in catalog.TIERS
    ]
    apps: dict[str, list[dict[str, Any]]] = {}
    for fn in catalog.FUNCTIONS:
        row = cfg["functions"].get(fn.id) or {}
        apps.setdefault(fn.app, []).append(
            {
                "id": fn.id,
                "label": fn.label,
                "blurb": fn.blurb,
                "suggested": fn.tier,
                "tier": config.function_tier(fn.id, cfg),
                "custom": "models" in row,
                "models": row.get("models", []),
                "first": next((e["label"] for e in config.route(fn.id, cfg) if health.available(e["id"], state)), ""),
            }
        )
    quota = _quota_status()
    return {
        "providers": providers,
        "models": models,
        "model_labels": {mid: e["label"] for mid, e in entries.items()},
        "tiers": tiers,
        "apps": [{"id": app, "label": catalog.app_label(app), "functions": fns} for app, fns in apps.items()],
        "warnings": config.warnings(cfg),
        "presets": [
            {"id": p.id, "provider": p.provider, "model": p.model, "label": p.label} for p in catalog.PRESETS
        ],
        "quota": quota,
        "any_ready": any(p["ready"] for p in providers),
        "gemini_free_tier": cfg["providers"]["gemini"]["free_tier"],
        # Inline in a <script> block: "<" escaped so a label can't close the tag.
        "state_json": json.dumps(
            {
                "models": [{k: m[k] for k in ("id", "provider", "model", "label", "free")} for m in cfg["models"]],
                "tiers": cfg["tiers"],
                "functions": cfg["functions"],
            }
        ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"),
    }


def _quota_status() -> dict[str, Any] | None:
    try:
        from stonepi_ai._files import ai_root
        from stonepi_vault.quota import get_quota

        return get_quota("llm", root=ai_root()).status()
    except Exception:  # noqa: BLE001
        return None


def apply_state(raw: str) -> dict[str, Any]:
    """Save the page's edited models / tiers / overrides (JSON from the page's script)."""
    try:
        data = json.loads(raw or "{}")
    except ValueError as exc:
        raise ValueError("The AI settings could not be read. Reload the page and try again.") from exc
    if not isinstance(data, dict):
        raise ValueError("The AI settings could not be read.")
    cfg = config.load()
    models = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        free = item.get("free")
        models.append(
            config.model_entry(
                str(item.get("provider") or ""),
                str(item.get("model") or ""),
                label=str(item.get("label") or ""),
                free=free if isinstance(free, bool) else None,
            )
        )
    cfg["models"] = models
    cfg["tiers"] = data.get("tiers") if isinstance(data.get("tiers"), dict) else cfg["tiers"]
    functions: dict[str, Any] = {}
    for fid, row in (data.get("functions") or {}).items():
        fn = catalog.function(fid)
        if fn is None or not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        if row.get("tier") in catalog.TIERS and row["tier"] != fn.tier:
            clean["tier"] = row["tier"]
        if isinstance(row.get("models"), list):
            clean["models"] = row["models"]
        if clean:
            functions[fid] = clean
    cfg["functions"] = functions
    return config.save(cfg)


def set_gemini_free(value: str) -> None:
    cfg = config.load()
    cfg["providers"]["gemini"]["free_tier"] = {"yes": True, "no": False}.get(value)
    # The answer is for the key, so it resets each Gemini model's own flag.
    for entry in cfg["models"]:
        if entry["provider"] == "gemini":
            entry["free"] = None
    config.save(cfg)


def set_ollama_url(url: str) -> None:
    cfg = config.load()
    cfg["providers"]["ollama"]["base_url"] = config.normalize_base_url(url)
    config.save(cfg)


def set_quota_limit(value: str) -> None:
    from stonepi_ai._files import ai_root
    from stonepi_vault.quota import get_quota

    limit = int(str(value).strip())
    if limit < 0:
        raise ValueError("The limit can't be negative.")
    get_quota("llm", root=ai_root()).set_limit(limit)
