---
name: new-stonepi-app
description: >-
  Scaffold and ship a new StonePi household app (FastAPI sibling of NewsCast/
  Pinboard/Studio/PriceScout). Use when creating a new app under apps/, wiring
  catalog/nginx/systemd/install/run-dev, copying chrome (theme, nav, settings
  hub/L2, icons, sheets, mobile), or fixing post-hoc mismatches with the portal
  shell. NewsCast is the full chrome baseline — see chrome.md.
---

# New StonePi app

StonePi apps are **household LAN products** on a Pi: shared SSO, Home launcher,
shared light/dark/palette theme, bottom nav on phones / side rail on desktop.

**Chrome baseline: NewsCast** (`apps/newscast`). Use Pinboard only for a tiny
shell. Never invent a new topbar, sheet, settings IA, or SVG size system — copy
NewsCast and strip product pages. Detail: [chrome.md](chrome.md). Portal IA:
[ux-stonepi](../ux-stonepi/SKILL.md). Deploy: [platform.md](platform.md).

## Workflow

1. **Name & route** — lowercase `app_id`, URL prefix (`/myapp/`), free port,
   systemd unit `stonepi-{id}`, service user. Record in [platform.md](platform.md).
2. **Scaffold** — `apps/{id}/` FastAPI + Jinja + static CSS/JS; clone chrome from
   **NewsCast** (`base.html`, `_theme_boot.html`, `_icons.html`, `_filter_icon.html`,
   confirm sheet, CSS token + shell block, sheet hoist JS). Strip product pages;
   keep shell.
3. **Product IA** — primary destinations in bottom nav (incl. **Home** → portal).
   Settings = product prefs + **About**; no Users/Updates when SSO is on.
4. **Platform wire** — catalog, nginx, systemd, `install.sh`, `run_dev.py`,
   dashboard icon, allowed ports, data dir (see [platform.md](platform.md)).
5. **Parity pass** — run the checklist below + [chrome.md](chrome.md) §15.
6. **Pi update** — use [push-to-pi](../push-to-pi/SKILL.md): package the overlay,
   then give the user `cmd /c "scripts\….cmd -Apply"` (never run scp/ssh yourself).

## Non-negotiables (fixes we keep re-doing)

| Topic | Rule |
|-------|------|
| **Baseline** | NewsCast is the source of truth for shell, settings hub/L2, sheets, SVG sizes, topbar actions. Pinboard = minimal only. |
| **Theme accent** | Primary buttons use `var(--accent)` / `var(--accent-ink)`. **Never** override `:root` accent to an app-only colour. Shared palettes: default brick, `ocean`, `forest`, `slate` × light/dark. |
| **Light/dark + palette** | Include `_theme_boot.html` (shared `stonepi-theme` / `stonepi-palette` cookies). Light/dark/auto toggle is **dashboard header only** — product apps apply the shared pref, do not ship `.theme-switch`. Colour palette: Dashboard → Settings → Appearance. Fonts: IBM Plex Sans + Source Serif 4. |
| **Topbar actions** | Order: brand → (status) → **global Refresh** (if app has sync) → **POST logout**. Refresh sits **next to** logout (`btn-refresh`); ≤420px icon-only. |
| **Nav** | Always include **Home** → `portal_home_url` / `stonepi_home_url` (not app `/`). Phone/tablet &lt;1024: bottom bar, `repeat(N, …)` where **N = actual links**. Laptop+ ≥**1024px**: sticky side rail (~200px). Never 800/900. |
| **Mobile shell** | `viewport-fit=cover`; `--safe-bottom`; `--tap: 44px` (40px ≤559px); phone: `html,body` overflow locked, `.main` scrolls; inputs ≥16px; confirm/filter/help sheets clear nav. Breakpoints: **720 / 1024 / 1440**. |
| **Sheets** | Hoist `.sheet` to `document.body` (`mountSheetsToBody`); `sheet-open` locks overflow; mobile bottom padding ~76–80px + safe-area; inner body scrolls. Never rely on `position:fixed` inside scrolling `.main`. |
| **SVG sizes** | ViewBox 24×24; CSS caps — nav/brand **22**, chrome actions **18**, theme **16**, chips **14**, hub glyph **18** in **40** tile. Always set width/height in CSS for the parent. Stroke `fill: none`; restore filled pips with `[fill]:not([fill="none"])` rules. See [chrome.md](chrome.md) §3. |
| **Icons** | `_icons.html` for actions; `_filter_icon.html` for nav/chips/settings. Settings gear = shared cog. Chips/hub rows: **icon + label** (never text-only). |
| **Buttons** | Labelled = `btn … btn-with-icon`. Icon-only = `icon-btn` + `aria-label` (use `icon-btn-quiet` for non-destructive). |
| **Settings IA** | Every viewport: NewsCast **hub → L2 (icons + subtexts) → one stacked panel**. Laptop+ keeps that drill-down (one-column hub; readable panel column) — no chip bar. `SETTINGS_PANEL_SUBTEXTS` required (no “Open this section”). Heading icons match L2. A single-card tab skips L2. View/chrome prefs under General → View options; export layout stays product-specific. SSO: no Users/Updates/password ownership. |
| **Confirms** | Shared sheet, not bare `confirm()`. Logout = **POST** + CSRF. |
| **CSRF** | Forms include `csrf_token`; cookie via `stonepi_auth.csrf`. |
| **Empty states** | Explain what belongs here + one CTA. |
| **Prefix** | Honor `STONEPI_PREFIX` for redirects and static under nginx path routing. |
| **Asset bust** | `?v={{ app_version }}-{{ asset_rev }}` + `fonts.css?v={{ fonts_rev }}`; `__asset_rev__` is computed from `static/` — never hand-bump. |
| **Copy source** | Clone CSS/HTML/JS chrome from NewsCast; delete product-only leftovers. |

## Ship checklist

Copy and tick while building:

```
Shell
- [ ] base.html: viewport-fit=cover, theme-color, _theme_boot, fonts, POST logout (no light/dark switch)
- [ ] Topbar order: brand → [Refresh] → bell (shared `alerts_bell`) → logout
- [ ] Confirm sheet present; JS wires data-confirm + mountSheetsToBody
- [ ] No app-specific --accent override after palette tokens
- [ ] btn-primary / links / focus rings use var(--accent)
- [ ] SVG size matrix applied (see chrome.md)

Nav
- [ ] Home → portal origin
- [ ] Column count = link count; labels fit on narrow phones
- [ ] Desktop rail ≥1024px; phone/tablet bottom bar + safe-area
- [ ] Every nav item has stroke icon (22×22)

Settings
- [ ] Phone: hub groups + L2 icons/subtexts + back link (if multi-tab)
- [ ] Desktop: chips with icons; all section cards visible
- [ ] section-heading icons match L2
- [ ] About: name, blurb, GitHub, version
- [ ] SSO: no Users/Updates/password ownership
- [ ] Save uses check icon; destructive actions iconed

Mobile
- [ ] Sheets clear bottom nav; inner scroll regions
- [ ] Filter long lists via sheet + chips-action when needed
- [ ] overflow-wrap on titles/meta; empty states
- [ ] App in `APP_CATALOG`; `__asset_rev__` computed (no literal `?v=`)

Notifications (see platform.md → Notifications)
- [ ] Events registered in stonepi_contracts catalog with an audience
- [ ] Every emit sets audience; personal events set user = Auth id (auth_user_id), never local ids
- [ ] add_shared_templates; bell via alerts_bell_state on every page (hidden standalone)
- [ ] Settings → Notifications: shared card via notifications_card_state (+ timing settings only)
- [ ] tests/test_personal_alerts.py

Platform (see platform.md)
- [ ] catalog.py entry (path, port, unit, icon, capabilities, launcher)
- [ ] nginx upstream + location
- [ ] systemd unit + install.sh user/venv/env/data
- [ ] run_dev.py port + vault list
- [ ] dashboard _icons.html + any badge map
- [ ] session allowed_ports includes app port
- [ ] /healthz (+ /api/display if Display card needed)
- [ ] README + CHANGELOG; gitignore runtime data/.venv
```

## Reference apps

| Need | Copy from |
|------|-----------|
| **Full chrome (default)** | `apps/newscast` |
| Minimal shell only | `apps/pinboard` |
| Chat + publish | `apps/studio` |
| Recent citizen + Pi push patterns | `apps/pricescout` |
| Portal settings / Health | `apps/dashboard` |

## Lessons from siblings (keep these)

| Lesson | From | Rule |
|--------|------|------|
| **Host-aware Home** | NewsCast / all | `portal_home_url(request, …)` — never hardcode `.local` only. |
| **`platform_managed`** | NewsCast, FileServe, EventTrakr | Hide Users / Updates / password / TLS ownership when SSO; link to Dashboard. |
| **Factory admin banner** | NewsCast, Dashboard | Surface in-UI if still `admin`/`admin`. |
| **Asset cache bust** | NewsCast | `?v={{ app_version }}-{{ asset_rev }}`. |
| **Settings hub + L2** | NewsCast | Grouped hub; L2 icons + short subtexts; desktop chips. See [chrome.md](chrome.md) §9. |
| **Sheet hoist / iOS** | NewsCast | Fixed sheets inside `.main` fail — hoist to `body`. |
| **SVG blow-ups** | NewsCast | Cap sizes in CSS; hub icons 18-in-40; never unstyled SVG. |
| **About is a chip with icon** | EventTrakr | Never text-only About. |
| **Capability defaults** | Dashboard Users | New users get Dashboard only; declare real `capabilities`. |
| **Display scrape privacy** | Pinboard / platform | Public nginx denies `/api/display`. |
| **Confirm sheet copy** | NewsCast / FileServe | Name the consequence; OK label = verb. |
| **Sign-out POST** | All | Never `<a href="/logout">`. |
| **Studio → FileServe** | Studio | `studio-fileserve` skill. |
| **Playwright / scrape apps** | EventTrakr | Browsers under service user `HOME=/opt/stonepi`. |
| **Vault secrets** | Dashboard / apps | UI says configured vs not set; never echo secrets. |
| **Empty + first useful action** | All | Data or one clear CTA on first screen. |

When unsure, open NewsCast and copy the mechanism — don’t re-invent.
