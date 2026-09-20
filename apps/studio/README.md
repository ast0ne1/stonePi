# Studio

**Part of [StonePi](../../README.md)** — chat-build static sites and publish them to FileServe.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/studio/ |
| Windows `run-dev.bat` | http://127.0.0.1:8005/studio/ |

Shared StonePi sign-in. Non-admins need grants with **Use LLM** and/or **Publish to FileServe** (`can_use_llm`, `can_publish`).

## Why it’s in StonePi

Family authoring without a laptop toolchain — kids and adults describe a SPA, guide, or game; Studio writes files; preview locally; publish onto the same LAN URLs as FileServe Hosted Pages.

## What to build

| Kind | Intent | Phone focus |
|------|--------|-------------|
| **Single Page App** | One-screen tool (checklist, quiz, converter) | Single column, large taps, safe-area |
| **Interactive Guide** | Step-by-step how-to or choose-your-path | One step per screen, large Next/Back |
| **Game** | Real playable game with score / play again | iPhone 13+ (~390×844) through laptop/desktop; on-screen + keyboard |

Guidelines injected every LLM turn: `app/prompts/fileserve_hosting.md` plus `kind_spa.md` / `kind_guide.md` / `kind_game.md`. Sites must work on desktop **and** small handsets (iPhone 13 mini class).

## What it does

- **Create** (landing): pick SPA / Guide / Game, name it, Start
- **Projects**: list and open existing work; chat, build, preview, publish
- Workspace files + live iframe preview
- Emits a JSON **file map** the app applies to disk
- **Publish to FileServe** — zip with the same options as Hosted Pages:
  - Label, description, path
  - Keep until (none / week / month / 3 months / 6 months / custom date)
  - Require a password (username + password)
- Republish updates the linked FileServe page when present

## Integrations

| Integration | Role |
|-------------|------|
| **AI / LLM** | Anthropic, OpenAI, or OpenAI-compatible base URL (`STUDIO_LLM_BASE_URL`). Keys from **Vault only**. |
| **FileServe** | Publish zip via authenticated `POST /api/studio/publish` |

## Hosting rules (summary)

- `index.html` at zip root; **relative** asset paths only
- External links `https://` only; no secrets in generated files
- Zip limits align with FileServe (file count / size; skip `__MACOSX` / `.DS_Store`)

See `.cursor/skills/studio-fileserve` and `app/prompts/`.

## Platform notes

- LLM keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, optional `STUDIO_LLM_BASE_URL` in Dashboard → Settings → Vault.
- Under StonePi, nginx serves `/studio/`; apps bind loopback.
- **Display / TRMNL:** `GET /api/display` returns project/build summary for Status wall (denied at nginx edge).

## Solo / standalone

Studio expects platform auth and FileServe for publish. Use `scripts\run-dev.bat` from the StonePi root.
