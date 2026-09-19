# Studio · kind guidelines (FileServe)

Shared hosting rules: `apps/studio/app/prompts/fileserve_hosting.md` (includes **mobile / iPhone 13 mini** baseline).

Per build type (injected every LLM turn after the user picks **What to build**):

| Kind id | File | Intent | Phone focus |
|---------|------|--------|-------------|
| `spa` | `kind_spa.md` | Single-page app / tool UI | Single column, sticky actions + safe-area |
| `guide` | `kind_guide.md` | Interactive how-to / story | One step per screen, large Next/Back |
| `game` | `kind_game.md` | Simple browser game | Keyboard + on-screen controls (no click-playfield-only) |

Studio system prompt = hosting rules + selected kind guideline + current file list.

Published zips land on the user’s FileServe Hosted Pages with the same keep-until / password options as FileServe itself.
