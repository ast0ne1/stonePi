"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2 (the body, so the user, is covered); nginx never
routes /pricewatch/api/internal/. Only the person's own watches, from what PriceWatch
already has: nothing here starts a scan, so a question never spends Bright Data quota.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db

router = APIRouter()

MAX_ITEMS = 8


def _session_secret() -> str:
    from app.routes import _session_secret as secret

    return secret()


def _day(raw: str | None) -> str:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone().strftime("%a %d %b")
    except ValueError:
        return ""


def _item(watch: dict) -> dict:
    currency = str(watch.get("currency") or "DKK")
    name = " ".join(p for p in (str(watch.get("product_name") or ""), str(watch.get("variant") or "")) if p)
    target = watch.get("target_price")
    lowest = watch.get("current_lowest")
    out = {
        "product": name[:120],
        "status": str(watch.get("status") or ""),
        "target": db.format_price(target, currency),
        "lowest_now": db.format_price(lowest, currency) if lowest is not None else "",
        "checked": _day(watch.get("last_checked_at")),
    }
    if target and lowest is not None and watch.get("status") != "strike_found":
        out["above_target_pct"] = round((float(lowest) - float(target)) / float(target) * 100, 1)
    strike = watch.get("strike") if watch.get("status") == "strike_found" else None
    if isinstance(strike, dict):
        out["deal"] = {
            "price": db.format_price(strike.get("watch_price"), currency),
            "shop": str(strike.get("retailer") or "")[:60],
            "found": _day(strike.get("found_at") or watch.get("strike_at")),
        }
    return out


def _gap(watch: dict) -> tuple[bool, float]:
    distance, target = watch.get("distance"), float(watch.get("target_price") or 0)
    return distance is None, (distance / target) if distance is not None and target else 0.0


def _get_price_alerts(user_id: str, _params: dict) -> dict:
    watches = db.list_watches(user_id)
    deals = [w for w in watches if w.get("status") == "strike_found"]
    # Watching: closest to the target first; ones with no price yet go last.
    waiting = [w for w in watches if w.get("status") in ("watching", "no_results", "error")]
    waiting.sort(key=_gap)
    items = [_item(w) for w in deals + waiting][:MAX_ITEMS]
    return {
        "total": len(watches),
        "deals": len(deals),
        "paused": sum(1 for w in watches if w.get("status") == "paused"),
        "watches": items,
    }


TOOLS = {"get_price_alerts": _get_price_alerts}


@router.post("/api/internal/agent")
async def agent_read(request: Request):
    from stonepi_auth.internal import verify_internal_v2

    body = await request.body()
    if not verify_internal_v2(_session_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Body must be JSON"}, status_code=400)
    tool = TOOLS.get(str(data.get("tool") or ""))
    user_id = str(data.get("user") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if tool is None or not user_id:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Unknown tool or no user"}, status_code=400)
    return {"ok": True, "payload": tool(user_id, params)}
