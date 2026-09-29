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
    <h1>Nothing built yet</h1>
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
    "spa": "Describe the app in the chat, then press Build — it will appear here.",
    "guide": "Describe the guide in the chat, then press Build — it will appear here.",
    "game": "Describe the game in the chat, then press Build — it will appear here.",
}

DEFAULT_GAME_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>My game</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <div class="shell">
    <header class="hud">
      <h1>My game</h1>
      <p class="hint">Describe the game in the chat, then press Build — it will appear here.</p>
      <p class="score" hidden>Score <span data-score>0</span></p>
    </header>
    <canvas id="game" width="360" height="480" aria-label="Game playfield"></canvas>
    <div class="controls" aria-label="On-screen controls">
      <button type="button" data-dir="left" aria-label="Left">◀</button>
      <button type="button" data-dir="up" aria-label="Up">▲</button>
      <button type="button" data-dir="down" aria-label="Down">▼</button>
      <button type="button" data-dir="right" aria-label="Right">▶</button>
      <button type="button" data-action aria-label="Action">A</button>
    </div>
  </div>
  <script src="app.js"></script>
</body>
</html>
"""

DEFAULT_GAME_CSS = """:root {
  --paper: #12141a;
  --ink: #f2f4f8;
  --accent: #5ee0a0;
  --pad: max(12px, env(safe-area-inset-left));
  --pad-r: max(12px, env(safe-area-inset-right));
  --pad-b: max(12px, env(safe-area-inset-bottom));
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  min-height: 100%;
  background: var(--paper);
  color: var(--ink);
  font-family: system-ui, sans-serif;
}
.shell {
  width: min(420px, 100%);
  margin: 0 auto;
  min-height: 100dvh;
  padding: max(12px, env(safe-area-inset-top)) var(--pad-r) var(--pad-b) var(--pad);
  display: grid;
  gap: 12px;
  align-content: start;
}
.hud h1 { margin: 0; font-size: 1.35rem; }
.hint, .score { margin: 0.25rem 0 0; color: #a8b0c0; font-size: 0.95rem; }
#game {
  width: 100%;
  height: auto;
  display: block;
  background: #1c2230;
  border-radius: 12px;
  touch-action: none;
}
.controls {
  display: grid;
  grid-template-columns: repeat(4, 1fr) 1.2fr;
  gap: 8px;
}
.controls button {
  min-height: 48px;
  min-width: 44px;
  border: 0;
  border-radius: 12px;
  background: #2a3344;
  color: var(--ink);
  font-size: 1.1rem;
  touch-action: manipulation;
}
.controls [data-action] { background: var(--accent); color: #102016; font-weight: 700; }
@media (min-width: 901px) {
  .shell { width: min(600px, 100%); padding-top: 24px; }
  .controls { max-width: 420px; margin: 0 auto; }
}
"""

DEFAULT_GAME_JS = """const canvas = document.getElementById("game");
const ctx = canvas?.getContext("2d");
if (ctx) {
  ctx.fillStyle = "#1c2230";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#5ee0a0";
  ctx.font = "16px system-ui";
  ctx.fillText("Your game will appear here after Build.", 24, 40);
}
console.log("StonePi Studio game shell — press Build after you describe the game.");
"""


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
    if kind == "game":
        files = {
            "index.html": DEFAULT_GAME_INDEX,
            "style.css": DEFAULT_GAME_CSS,
            "app.js": DEFAULT_GAME_JS,
        }
    else:
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


_PLACEHOLDER_NAMES = {"game": "My new game", "guide": "My new story", "spa": "My new app"}


def owner_id(project: dict) -> str:
    """Auth user id that owns the project; "" for projects made before ownership existed."""
    return str(project.get("owner_id") or "")


def claim_unowned(owner: str, owner_name: str) -> int:
    """Give every ownerless (pre-ownership) project to ``owner``. Returns how many moved."""
    if not owner:
        return 0
    data = _load()
    moved = 0
    for item in data["projects"]:
        if not owner_id(item):
            item["owner_id"] = owner
            item["owner_name"] = owner_name
            moved += 1
    if moved:
        _save(data)
    return moved


def refresh_owner_name(owner: str, owner_name: str) -> None:
    """Keep the stored label in step when someone changes their display name."""
    if not owner or not owner_name:
        return
    data = _load()
    changed = False
    for item in data["projects"]:
        if owner_id(item) == owner and item.get("owner_name") != owner_name:
            item["owner_name"] = owner_name
            changed = True
    if changed:
        _save(data)


def create_project(name: str, *, kind: str = "spa", owner: str = "", owner_name: str = "") -> dict:
    kind_id = (kind or "spa").strip().lower()
    if kind_id not in BUILD_KIND_IDS:
        kind_id = "spa"
    clean = (name or "").strip()
    project = {
        "id": uuid.uuid4().hex[:12],
        "name": clean or _PLACEHOLDER_NAMES.get(kind_id, "My new project"),
        # No name yet: the AI proposes a title on the first build and we adopt it.
        "auto_name": not clean,
        "kind": kind_id,
        "owner_id": owner,
        "owner_name": owner_name,
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


def append_message(project_id: str, role: str, content: str, *, note: str = "") -> dict | None:
    data = _load()
    for item in data["projects"]:
        if item.get("id") != project_id:
            continue
        entry = {"role": role, "content": content, "at": _now()}
        if note:
            entry["note"] = note
        item.setdefault("messages", []).append(entry)
        item["updated_at"] = _now()
        _save(data)
        return item
    return None


NAME_MAX = 80


def clean_name(name: str) -> str:
    """Collapse whitespace and cap to NAME_MAX, the same limit the share sheet uses."""
    return " ".join((name or "").split())[:NAME_MAX]


def rename_project(project_id: str, name: str) -> str | None:
    """Set a name the user chose. Clears auto_name so the next build won't overwrite it."""
    clean = clean_name(name)
    if not clean:
        return None
    data = _load()
    for item in data["projects"]:
        if item.get("id") != project_id:
            continue
        item["name"] = clean
        item["auto_name"] = False
        item["updated_at"] = _now()
        _save(data)
        return clean
    return None


def adopt_name(project_id: str, name: str) -> str | None:
    """Rename a still-unnamed project to the title the build chose. Returns the new name."""
    clean = clean_name(name)
    if not clean:
        return None
    data = _load()
    for item in data["projects"]:
        if item.get("id") != project_id or not item.get("auto_name"):
            continue
        item["name"] = clean
        item["auto_name"] = False
        item["updated_at"] = _now()
        _save(data)
        return clean
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


def set_fileserve_page_id(
    project_id: str, page_id: int | None, *, public_path: str | None = None, description: str | None = None
) -> None:
    data = _load()
    for item in data["projects"]:
        if item.get("id") == project_id:
            item["fileserve_page_id"] = page_id
            if public_path:
                item["public_path"] = public_path
            if description is not None:
                item["share_description"] = description
            item["published_at"] = _now()
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
