# Games

Each game is **one folder** under `games/`. That folder is the source of truth — edit there, then pack a zip for FileServe.

## Layout

```
games/
  labyrinth/          ← one game
    index.html        ← required (FileServe Site entry)
    css/
    js/
  pack.py             ← zip a game for upload
  README.md
```

Rules:

- Every game folder must contain `index.html` at its root (or FileServe will reject the zip).
- Keep assets relative (`css/…`, `js/…`) so the site works under `/slug/`.
- Do not put multiple games in one folder.

## Pack for FileServe

From the repo root:

```powershell
python games/pack.py labyrinth
```

Writes `dist/labyrinth.zip`. Upload that zip on **Add Page** in FileServe (type **Site**).

Pack every game:

```powershell
python games/pack.py --all
```
