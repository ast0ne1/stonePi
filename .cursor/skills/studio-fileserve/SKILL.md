---
name: studio-fileserve
description: FileServe hosting rules for StonePi Studio generated sites (index.html, relative paths, zip limits, file map JSON). Use when editing Studio projects or prompts under apps/studio.
---

# StonePi Studio · FileServe hosting

Canonical rules live in `apps/studio/app/prompts/fileserve_hosting.md`. Summary:

## Must-haves

- **`index.html`** at the project root (FileServe site entry).
- **Relative asset paths** only (`style.css`, `./images/x.png`). No root-absolute `/static/...` paths.
- **`https://` only** for external URLs in generated HTML/CSS/JS.
- **Phone-first**: design for ~375px (iPhone 13 mini class) — viewport meta, 44px taps, safe-area insets, no horizontal scroll. Per-kind mobile rules in `kind_spa.md` / `kind_guide.md` / `kind_game.md`.
- **Games**: keyboard + dedicated on-screen controls; never click-on-playfield as the only scheme (`kind_game.md`).

## Limits

- Max **2500** files, **200 MB** uncompressed in the publish zip.
- No `__MACOSX/`, `.DS_Store`, or `Thumbs.db`.

## Publish (FileServe parity)

Published sites live under the user’s FileServe Hosted Pages. Studio publish must offer the same **Keep until** (none / week / month / 3m / 6m / custom date) and **Require a password** (username + password) options as FileServe’s page setup form.

## Model output

Studio applies a JSON **file map** from the assistant (see hosting prompt). Every chat turn also injects the selected **build kind** guideline (`kind_spa.md` / `kind_guide.md` / `kind_game.md`).
