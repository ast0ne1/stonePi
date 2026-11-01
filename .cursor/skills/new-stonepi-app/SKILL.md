---
name: new-stonepi-app
description: >-
  Scaffold and ship a new StonePi household app (FastAPI sibling of NewsCast,
  Ripple, Pinboard). Use when creating a new app under apps/, wiring catalog,
  nginx, systemd, install, backup, Recover, CLI and run-dev, building the shell
  (shared header, bottom nav / side rail, settings hub, sheets, toasts), or
  fixing an app that has drifted from the shared shell.
---

# New StonePi app

StonePi apps are **household LAN products** on a Pi: shared sign-in (Auth),
the shared header (search, bell, app switcher, avatar menu), the design-system
tokens and fonts from `packages/stonepi_brand`, a bottom nav on phones and a
side rail on laptops. Since platform 0.2.0 (F6, `ideation/ui-refresh.md`) the
look comes from the design system, not from each app.

**References** (copy mechanisms, don't invent):

| Need | Copy from |
|------|-----------|
| Shell, nav, settings hub → L2 → panel, toasts, sheets, JS-submitted forms | `apps/newscast` |
| Small, platform-only app skeleton: config, `_gate`, `_render`, CSRF, denied page, admin-only settings, signed internal calls, tests | `apps/ripple` (newest, 0.5.0) |
| Add sheets from a header "+", picker sheet, outbound fetch safety (`links.py`), capabilities | `apps/pinboard` |
| Portal settings / Health / Services | `apps/dashboard` |

NewsCast is older than the platform and still carries its own solo-mode user
system (`app/auth.py`, local users, `/login`), i18n and its own `routers/`
package. Don't copy those into a new app: new apps are platform-only and follow
Ripple for the Python side. Detail: [chrome.md](chrome.md) (UI shell) and
[platform.md](platform.md) (registration and wiring). Portal IA and breakpoints:
[ux-stonepi](../ux-stonepi/SKILL.md).

## Workflow

1. **Identity** — lowercase `app_id`, URL prefix (`/myapp/`), free port, unit
   `stonepi-{id}`, service user, data dir. Fill the identity sheet in
   [platform.md](platform.md).
2. **Scaffold** — `apps/{id}/` with the file layout in [platform.md](platform.md):
   FastAPI + Jinja + `static/css/app.css` + `static/js/app.js`. Shell from
   NewsCast (`base.html`, `_theme_boot.html`, `_brand_mark.html`, `_icons.html`,
   confirm sheet, toast), Python skeleton from Ripple (`config.py`, `main.py`,
   `serve.py`, `routes.py`).
3. **Product IA** — 2–3 primary destinations + **Settings last** in the bottom
   nav (max 4). **No Home item**: Home is the top entry of the header's app
   switcher. Adding things happens in sheets opened from the header "+". Settings
   = product preferences + **About**.
4. **Register** — every place in [platform.md](platform.md) → Registration
   checklist (catalog, prefix roots, safe_next ports, contracts label, icons,
   nginx ×2, systemd + target, installer, backup, Recover, CLI, run-dev, README).
5. **Tests** — at least the Ripple set (see [platform.md](platform.md) → Tests),
   plus `packages/stonepi_auth/tests` (platform consistency) green.
6. **Parity pass** — the checklist below + [chrome.md](chrome.md) §14; the
   responsive widths in `deploy/responsive-audit.md`.
7. **run-dev, then Pi** — check in `run-dev.bat` first; then prepare the overlay
   with [push-to-pi](../push-to-pi/SKILL.md) and give the user the
   `cmd /c "scripts\….cmd -Apply"` line (never run scp/ssh yourself).

## Non-negotiables (fixes we keep re-doing)

| Topic | Rule |
|-------|------|
| **No Home in the nav** | Home (Dashboard) lives in the shared header's app switcher (`stonepi/header.html`). The bottom nav holds the app's own destinations only; the brand links to the app's own `/`. |
| **Nav** | 2–3 tabs + Settings last, max 4. Bottom bar below 1024px, `repeat(N, minmax(0, 1fr))` with **N = actual links**; sticky side rail from **1024px**. Never 800/900. |
| **Shared header** | Topbar actions end with `header_tools(...)` from `stonepi/header.html` (bell, switcher, avatar with Appearance and Log out). Optional `header_search(...)` and at most one primary action (Refresh) before it. Never hand-build a bell, logout button or theme switch. |
| **Design system** | `brand_head(app_id, fonts_rev)` before `app.css`, `brand_tail(fonts_rev)` after it. Colours, fonts, radii come from `tokens.css`; never define colour tokens or hex values in `app.css`. Primary = `--primary` (alias `--accent`). |
| **Theme** | Include `_theme_boot.html` (shared `stonepi-theme` cookie: light / dark / system). The choice is made in the avatar menu → Appearance or Dashboard → Settings → General. No per-app switch, no colour palettes (removed in 0.2.0). |
| **Icons** | App icon = fixed artwork `/assets/brand/icons/apps/{id}.svg` (+ PNGs). UI glyphs = outline SVGs on a 24 grid, sized in CSS (nav 20px, buttons 18px). See [chrome.md](chrome.md) §3. |
| **Input in sheets** | Adding things: header "+" (`header_fab(href, label, sheet="name")`) opens a `.sheet[data-sheet="name"]` with the form; several kinds → a picker sheet first. No inline add forms on main pages. Settings panels are the exception. |
| **Confirms** | Destructive actions: `form[data-confirm]` + the shared confirm sheet in `base.html`. Never `window.confirm()`. OK label = the verb. |
| **Flash** | Done-messages fade as a toast (`[data-toast]`), never a `.banner-ok`; errors stay visible (inline `[data-form-error]` or `.banner`). Background posts: `X-Requested-With: fetch` → `{ok, message, redirect?}`. See [chrome.md](chrome.md) §9. |
| **Settings IA** | Hub (groups of rows: icon, title, subtext, chevron) → L2 list when a tab has more than one panel → one stacked panel. Same drill-down on every viewport. Every row has a subtext. About last. |
| **Auth** | Every page and POST goes through one gate (Ripple `_gate`): signed out → Auth login; no grant → `denied.html` (403). Access is re-checked on every request from the session cookie. |
| **Admin-only** | Household settings: admins only, checked server-side on the POST. Members don't see household settings, only their own personal ones (if the app has any). |
| **CSRF** | Every POST: `csrf_ok(request.cookies.get(CSRF_COOKIE), token)` or `csrf_ok_request(request.cookies, token)`. `csrf_ok` takes the cookie **value**; passing the cookies dict makes every form fail. |
| **Per-person data** | Key it by the Auth user id (`user.user_id`), never username or a local id. |
| **Redirects** | `next` values: same-app paths only (`/x`, not `//x`, no scheme). Cross-app: `stonepi_auth.session.safe_next`. |
| **Internal calls** | Service-to-service under `/api/internal/*`, signed with `stonepi_auth.internal` v2; nginx blocks the path at the edge. Never expose an unsigned write endpoint. |
| **Display feed** | `/api/display` is loopback-only (Dashboard tile, TRMNL, Car Thing); add the app to `stonepi-public-deny-display.conf`. |
| **Platform lock** | `add_platform_lock(app, lambda: routes._session_secret())` in `main.py`: a Pi without a session secret returns 503 instead of running open. Every new app has it; only NewsCast, EventTrakr and FileServe (built standalone, with their own local sign-in) go without. |
| **Prefix** | `PrefixMiddleware` when `STONEPI_PREFIX` is set; write root paths (`/settings`) in templates and redirects and let the middleware rewrite them. JSON isn't rewritten: build URLs for JSON with `prefix_path`. |
| **Cache bust** | `?v={{ asset_rev }}` (computed from `static/`) and `fonts_rev`. Never a typed date or number: `test_platform_consistency.py` fails on it. |
| **Versions** | `__version__` in `app/__init__.py` is the only place. User apps share the 0.0.x line; only the feature app gets a number while staging. See [platform.md](platform.md) → Versioning. |
| **Outbound fetches** | Anything fetched from a user-given URL: public addresses only, ports 80/443, every redirect re-checked, size caps, raster images only. Pinboard `app/links.py`. |
| **Empty states** | Say what belongs here + one clear action. |

## Ship checklist

Copy and tick while building:

```
Shell (chrome.md)
- [ ] base.html: viewport-fit=cover, theme-color, _theme_boot, brand_head/brand_tail around app.css
- [ ] Topbar: brand (app icon + name → /) · [header_search] · [one primary action] · header_tools
- [ ] header_tools gets csrf_token (or local_name standalone); render it on every page
- [ ] No colour tokens / hex in app.css; primary buttons use the shared .btn-primary
- [ ] Confirm sheet + toast in base.html; sheets hoisted to <body>
- [ ] Add flows in sheets from header_fab; Cancel = data-close-sheet; first field data-autofocus
- [ ] Glyph sizes set in CSS (nav 20, buttons 18, hub 18-in-40)

Nav
- [ ] No Home item; Settings last; 3–4 items; N columns = N links
- [ ] Bottom bar + safe-area below 1024; side rail from 1024
- [ ] Every nav item: outline glyph + short label

Settings
- [ ] Hub groups → (L2) → one stacked panel; back link; subtexts on every row
- [ ] Panel heading icons match the hub / L2 icons
- [ ] Admin-only saves checked server-side; household settings hidden from members
- [ ] About: name, blurb, GitHub, Version (from __version__)
- [ ] Notifications tab only if the app emits events (shared card)

Platform (platform.md)
- [ ] Every row of the Registration checklist
- [ ] /healthz; /api/display only if it feeds Home/TRMNL/Car Thing (+ public deny)
- [ ] add_platform_lock; PrefixMiddleware; mount_brand_fonts
- [ ] Data under STONEPI_DATA_DIR; nothing runtime in git or zips
- [ ] App README + CHANGELOG ("Unreleased" until the cut); root CHANGELOG Unreleased + README tables

Tests
- [ ] App tests (gate, CSRF, admin-only, per-person, healthz/display)
- [ ] packages/stonepi_auth/tests green (consistency, prefix, icons)

Responsive
- [ ] 375 / 800 / 1280 / 1440+ (deploy/responsive-audit.md); sheets clear the nav
```

## Lessons from siblings (keep these)

| Lesson | From | Rule |
|--------|------|------|
| **Host-aware Home** | all | `portal_home_url(request, env.public_origin)`; never hardcode `.local`. Cross-app links via `stonepi_auth.header.app_page_url`. |
| **Factory admin banner** | NewsCast, Pinboard | `factory_admin_warning(user)` → a `.banner-muted` pointing at Dashboard → Settings → General. |
| **Sheet hoist / iOS** | NewsCast | Fixed sheets inside scrolling `.main` fail on iOS; hoist to `body`. |
| **Unstyled SVG blow-ups** | NewsCast | Every SVG parent has a CSS width/height. |
| **Capability defaults** | Dashboard People | New users get Dashboard only; declare only real `capabilities`. |
| **Display privacy** | Pinboard / platform | Public nginx denies `/api/display`; `/api/internal/` is blocked for every app. |
| **Display page capture** | Ripple | A display *page* (not JSON) opens for Notify only with a one-off key from a signed `/api/internal/capture-key`; catalog `capture_key` / `display_page`. |
| **Confirm sheet copy** | NewsCast / Ripple | Name the consequence; OK label = verb ("Delete"). |
| **Log out** | all | Through `header_tools` (POST form); the app's `/logout` redirects to Auth's `logout_url`. Never an `<a href="/logout">`. |
| **Icons need PNGs** | Ripple, Radio | Run `render_icons.py` after adding the SVG; the consistency test checks. |
| **Playwright / scrape apps** | EventTrakr | Browsers under the service user `HOME=/opt/stonepi`; `install_app {id} {user} playwright`. |
| **Vault secrets** | Dashboard / apps | Read the session secret Vault-first (Ripple `_session_secret`); UI says configured vs not set; never echo secrets. |

When unsure, open NewsCast (UI) or Ripple (Python) and copy the mechanism.

## Decided 2026-10-31 (where NewsCast and Ripple differed)

The rules above follow these. Until the apps are brought into line, don't copy the "still differs" behaviour. (Audited against the code 2026-10-31.)

| # | Decision | Still differs |
|---|----------|---------------|
| 0 | **Nav: no Home item, Settings last**, at most 4 items | None (all apps match) |
| 1 | **Done-messages are a toast in every app**, never `.banner-ok` | SportGuide, PriceScout, Pinboard, Ripple, Studio (banners); EventTrakr, FileServe (both, by flash category) |
| 2 | **Background posts the standard way in every app**: `X-Requested-With: fetch` → `{ok, message, redirect?}` | Ripple (`x-ripple-ajax`); EventTrakr, PriceWatch, Library don't use the header |
| 3 | Every browser POST checks CSRF | EventTrakr (login only; its forms don't send a token). NewsCast fixed 2026-10-31 (uncommitted) |
| 4 | **Every app calls `add_platform_lock`**, except NewsCast, EventTrakr and FileServe: built standalone, they keep their local sign-in as the fallback | PriceWatch, Library |
| 5 | Home apps' rail: `var(--app-rail-width)` = 200px; the Dashboard keeps `--sidebar-width` 240px | Every Home app hard-codes it (200px, fine in effect); NewsCast overrides to 220px |
| 6 | *Open:* adding things: header "+" opens a sheet; no "New …" nav tab (sheets stay the rule for new apps; existing apps not moved yet) | "+" opens a page: EventTrakr, PriceWatch, Ripple, Studio, FileServe; Ripple also has a "New post" tab. Pinboard and Radio use sheets |
| 7 | *Open:* `header_tools` rendered on every page, signed in or not | SportGuide, Pinboard, Ripple, Library, Studio (`{% if user %}`) |
| 8 | **Members don't see household settings**, only their own personal ones | Pinboard, Ripple, PriceScout show them read-only; others to check (NewsCast, FileServe, PriceWatch already hide them) |
| 9 | Admins open every app without a grant | `PlatformUser.can_access` (admins go through the apps list) |
| 10 | Keep the `{prefix}/static` mount next to `/static`: nine apps have it, so it's the pattern, not a stray | NewsCast and EventTrakr don't have it (fine as they are) |

A new user app starts one above the highest Home app version at the time; confirm the number with the owner.
