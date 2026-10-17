# Changelog — Studio

## 0.0.9 — 2026-10-17

### Changed
- **Studio's models are set up for the whole Pi** (backlog F13), in Dashboard → Settings → AI, instead of Studio's own Model picker. Each build kind is its own AI job: **Handy apps** (Standard tier), **Stories and guides** (Cheap) and **Games** (Best). If a model is rate limited, out of quota or down, Studio moves on to the next one in the order, as long as nothing has been written yet. Gemini and Ollama now work too. Settings → Model shows which models each kind uses and links to Dashboard. Who may use AI is unchanged (Studio's AI permission per person).
- **Your earlier choice carries over:** on first start, the Model picker's service and models (`settings.json`) become each build kind's own list in the platform AI config, in the same order. Builds keep using them until you change them in Dashboard. This only happens if the platform AI config doesn't exist yet.
- `STUDIO_LLM_BASE_URL` is no longer read: add a local or OpenAI-compatible server as Ollama on Dashboard → Settings → AI. `STUDIO_LLM_MAX_TOKENS` still caps Claude replies; OpenAI-style models write up to their own limit.

### Removed
- Studio's own Model picker (`app/model_settings.py`, `POST /settings/model`, which now just returns to the page).

## 0.0.8 — 2026-10-11

### Changed
- **New look** (StonePi design system): Studio icon in the header and as favicon and home-screen icon; new colours, fonts, buttons and cards in light and dark; the old palettes are gone.
- **New shared StonePi header.** The top bar now has the notifications bell, an app switcher (grid icon) and your avatar, the same as every StonePi app. Sign out moved into the avatar menu, under **Log out**, next to Light / Dark / System appearance and Manage account.
- **Lighter bottom bar: Projects · Settings.** Home is replaced by the app switcher in the header; Create is now a floating **+** button on the Projects page (it still opens the same Create screen, now at `/new`, with a "← Projects" link back and the Projects tab highlighted). The "Start something new" tile is gone, so the + is the one way to start.
- **Studio opens on Projects** once you have any; the first time (no projects yet) it still opens on the Create screen.

## 0.0.7 — 2026-10-04

### Security
- On a platform install with no session secret every page returns 503 instead of opening up (solo runs unchanged).

### Changed
- Both permissions declare their default (off) explicitly in the catalog; no behaviour change.

## 0.0.6 — 2026-09-29

### Security
- **Projects belong to whoever made them.** Opening, previewing, chatting, renaming, deleting and publishing now only work on your own projects; another member's project reads as not found. Admins keep full access and get **My projects / All projects** on the Projects page, with a filter by person. Projects made before this change move to the first admin who opens Studio. A guard test fails if a new project route skips the check.

### Fixed
- Mobile chat starts at the top of the message box after you pick a create type, so the assistant intro is on screen without scrolling up
- **Preview never updated after Build.** Replies were capped at 8k tokens, so full games were cut off, the JSON file map failed to parse, and nothing was written, yet the UI still said "Live". Builds now stream with a 64k budget (`STUDIO_LLM_MAX_TOKENS`) and write raw `<studio-file path="…">` blocks (no JSON escaping). A reply that gets cut off changes nothing and says so.
- Rebuilds now send the current file contents to the model, so it edits the real code instead of guessing at it.
- Preview responses are `no-store`, so a rebuilt `app.js` / `style.css` is never served stale.

### Changed
- Cache-busting is automatic: `?v=` tokens are a hash of `app/static/` (and the shared fonts), so CSS/JS changes no longer need a hand-bumped `__asset_rev__`.
- Remaining form POST handlers (settings, create/delete project, logout) are sync `def` so blocking work runs in FastAPI's threadpool; `project_chat` stays async for streaming
- Publish to FileServe runs as a sync route (threadpool) so the long zip/upload does not block Studio's event loop
- Create page on laptop+ fits hero, kind cards, and how-it-works in one viewport (no page scroll)
- Create cards are Start-only (no name field); the AI names the project on first build (rename it with the pencil any time). On phones, how-it-works sits under the hero so the four steps are seen before the kind rows; laptop+ still keeps those steps at the bottom
- Laptop/desktop Create: kind cards hug their content (shorter art band, Start under the blurb); steps sit directly under the cards (no pinned-to-bottom gap); create column capped ~960–1040px so the three cards stay denser
- Default models: `claude-sonnet-5` and `gpt-4.1` (`gpt-4o-mini`'s ~16k reply limit cut full games off). Env vars `STUDIO_LLM_MODEL` / `STUDIO_OPENAI_MODEL` still set the defaults.

### Added
- Settings shows the Auth factory-password banner (links to StonePi → Settings → General) when SSO still has admin/admin
- Publish alerts via Notify (`studio.site_published`) when a game/app/guide is shared to FileServe; Settings → Notifications points at Destinations / Event prefs
- Chat-style editor: message bubbles, Enter to send, auto-growing composer, starter ideas, a Describe → Build → Check → Publish stepper, and a Stop button.
- Live progress while the model works: "Message received…", streamed reply text, per-file "Writing app.js · 12 KB", elapsed timer, and a building overlay on the preview.
- Preview guardrail: a probe injected only into the preview reports runtime and load errors (file:line) and blank pages. Static checks catch missing or absolute asset paths and truncated files. **Ask Studio to fix** sends the problems back as a build, and Publish asks for confirmation while problems are open.
- Preview sizes: Fit, Phone (true 390×844), and Laptop (1280×800), scaled to the pane, plus reload and open-in-tab.
- **Rename a project** any time: tap the pencil next to its name in the editor, or next to its name on its card in Projects (same small pencil on phones, tablets and desktop, so it reads as rename, not edit card). A name you choose stays put; the AI only names projects you haven't named yourself.

- **Settings → Model** (admins): choose the AI service (Automatic / OpenAI / Anthropic, with key status from the Vault), the OpenAI model ID, and the Claude model (Sonnet 5, Opus 5, Fable 5.1, Haiku 4.5 or a custom ID). Saved in `DATA_DIR/settings.json`, used from the next message.

## 0.0.5 — 2026-09-22

### Changed
- Responsive shell: side rail at **≥1024px**
- Editor split panes: chat + preview fill short laptop heights (≥1024) without stacking until narrower viewports
- Laptop/desktop: main / editor column fills width beside the rail; Settings cards use multi-column layout; Projects list 2–3 columns

## 0.0.4 — 2026-09-20

### Added
- Settings → About; admins can view/edit LLM prompt markdown under Settings → Prompts (`prompt_files` + DATA_DIR overrides)

### Changed
- Settings tab chrome matches other apps (About flush; pill chips)

## 0.0.3 — 2026-09-20

### Changed
- Nav: **Home → Create → Projects**. Landing (`/`) is Create only; project list lives under `/projects`
- Bottom nav is three equal columns on phone
- Stronger Game kind prompts and default game shell for Build/preview

## 0.0.2 — 2026-09-19

### Added
- `GET /api/display` — project counts / build line for Dashboard Status wall (loopback scrape; nginx denies at the edge)

## 0.0.1 — initial

- Chat-build SPA / Guide / Game; publish to FileServe
