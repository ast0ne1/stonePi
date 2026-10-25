"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2 (the body, so the user, is covered); nginx never
routes /pinboard/api/internal/. Reminders: the user's own plus unassigned household ones
(muse.md §7.1), never ones assigned to someone else. Notices: the household's.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import store

router = APIRouter()

MAX_ITEMS = 10


def _session_secret() -> str:
    from app.routes import _session_secret as secret

    return secret()


def _get_reminders(user_id: str, _params: dict) -> dict:
    today = date.today()
    mine: list[dict] = []
    for rem in store.list_items()["reminders"]:
        text = str(rem.get("text") or "").strip()
        owner = str(rem.get("assignee_user") or "").strip()
        if not text or (owner and owner != user_id):
            continue
        due = str(rem.get("due") or "").strip()[:10]
        mine.append({
            "text": text[:200],
            "due": due,
            "due_label": store._due_relative(due, today),
            "overdue": bool(due) and due < today.isoformat(),
            "for": "you" if owner else "household",
        })
    mine.sort(key=lambda r: (r["due"] or "9999-12-31", r["text"]))
    return {"total": len(mine), "reminders": mine[:MAX_ITEMS]}


def _get_notices(_user_id: str, _params: dict) -> dict:
    notices = list(store.list_items()["notices"])
    notices.sort(key=lambda n: str(n.get("created_at") or ""), reverse=True)
    out = []
    for notice in notices[:MAX_ITEMS]:
        created = str(notice.get("created_at") or "")
        try:
            posted = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().strftime("%a %d %b")
        except ValueError:
            posted = ""
        out.append({"text": str(notice.get("text") or "")[:300], "posted": posted, "created_at": created})
    return {"total": len(notices), "notices": out}


TOOLS = {"get_reminders": _get_reminders, "get_notices": _get_notices}


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
