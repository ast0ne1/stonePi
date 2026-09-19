from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import BUILD_KIND_IDS, DATA_DIR, WORKSPACE_DIR

STORE = DATA_DIR / "studio.json"
DEFAULT_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>My page</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <main>
    <h1>Hello from StonePi Studio</h1>
    <p>Ask the assistant to build your site.</p>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""
DEFAULT_CSS = "body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 40rem; }\n"
DEFAULT_JS = "console.log('StonePi Studio preview');\n"

_KIND_TITLES = {"spa": "My app", "guide": "My guide", "game": "My game"}
_KIND_INTROS = {
    "spa": "Ask Studio what this app should do.",
    "guide": "Ask Studio to write the first steps.",
    "game": "Ask Studio how the game should play.",
}


def _load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE.exists():
        return {"projects": []}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": []}
    return {"projects": list(data.get("projects") or [])}


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_workspace(project_id: str, *, kind: str = "spa") -> Path:
    root = WORKSPACE_DIR / project_id
    root.mkdir(parents=True, exist_ok=True)
    title = _KIND_TITLES.get(kind, "My page")
    intro = _KIND_INTROS.get(kind, "Ask the assistant to build your site.")
    index = DEFAULT_INDEX.replace("My page", title).replace(
        "Ask the assistant to build your site.", intro
    )
    files = {
        "index.html": index,
        "style.css": DEFAULT_CSS,
        "app.js": DEFAULT_JS,
    }
    for name, body in files.items():
        path = root / name
        if not path.exists():
            path.write_text(body, encoding="utf-8")
    return root


def list_projects() -> list[dict]:
    return _load()["projects"]


def get_project(project_id: str) -> dict | None:
    for item in _load()["projects"]:
        if item.get("id") == project_id:
            return item
    return None


def create_project(name: str, *, kind: str = "spa") -> dict:
    clean = (name or "").strip() or "Untitled"
    kind_id = (kind or "spa").strip().lower()
    if kind_id not in BUILD_KIND_IDS:
        kind_id = "spa"
    project = {
        "id": uuid.uuid4().hex[:12],
        "name": clean,
        "kind": kind_id,
        "created_at": _now(),
        "updated_at": _now(),
        "fileserve_page_id": None,
        "messages": [],
    }
    data = _load()
    data["projects"].insert(0, project)
    _save(data)
    _seed_workspace(project["id"], kind=kind_id)
    return project


def append_message(project_id: str, role: str, content: str) -> dict | None:
    data = _load()
    for item in data["projects"]:
        if item.get("id") != project_id:
            continue
        item.setdefault("messages", []).append({"role": role, "content": content, "at": _now()})
        item["updated_at"] = _now()
        _save(data)
        return item
    return None


def mark_built(project_id: str) -> None:
    data = _load()
    for item in data["projects"]:
        if item.get("id") == project_id:
            item["built"] = True
            item["built_at"] = _now()
            item["updated_at"] = _now()
            _save(data)
            return


def set_fileserve_page_id(project_id: str, page_id: int | None) -> None:
    data = _load()
    for item in data["projects"]:
        if item.get("id") == project_id:
            item["fileserve_page_id"] = page_id
            item["updated_at"] = _now()
            _save(data)
            return


def delete_project(project_id: str) -> bool:
    data = _load()
    before = len(data["projects"])
    data["projects"] = [item for item in data["projects"] if item.get("id") != project_id]
    if len(data["projects"]) == before:
        return False
    _save(data)
    root = WORKSPACE_DIR / project_id
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    return True


def workspace_root(project_id: str) -> Path:
    root = WORKSPACE_DIR / project_id
    if not root.is_dir():
        raise FileNotFoundError("Project workspace missing.")
    return root
