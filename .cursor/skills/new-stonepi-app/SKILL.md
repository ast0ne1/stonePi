---
name: new-stonepi-app
description: >-
  Scaffold and ship a new StonePi household app (FastAPI sibling of NewsCast/
  Pinboard/Studio/PriceScout). Use when creating a new app under apps/, wiring
  catalog/nginx/systemd/install/run-dev, copying chrome (theme, nav, settings
  chips, icons, mobile), or fixing post-hoc mismatches with the portal shell.
---

# New StonePi app

StonePi apps are **household LAN products** on a Pi: shared SSO, Home launcher,
shared light/dark/palette theme, bottom nav on phones / side rail on desktop.
Copy chrome from a living sibling — prefer **Pinboard** (small) or **NewsCast**
(full) — never invent a new shell.

Also read [ux-stonepi](../ux-stonepi/SKILL.md) for portal IA. Detail refs:
[chrome.md](chrome.md) (UI/theme/mobile/icons) · [platform.md](platform.md)
(wiring/deploy).

## Workflow

1. **Name & route** — lowercase `app_id`, URL prefix (`/myapp/`), free port,
   systemd unit `stonepi-{id}`, service user. Record in [platform.md](platform.md).
2. **Scaffold** — `apps/{id}/` FastAPI + Jinja + static CSS/JS; clone chrome from
   sibling (`base.html`, `_theme_boot.html`, `_icons.html`, confirm sheet, CSS
   token block). Strip product pages; keep shell.
3. **Product IA** — primary destinations in bottom nav (incl. **Home** → portal).
   Settings = product prefs + **About**; no Users/Updates when SSO is on.
4. **Platform wire** — catalog, nginx, systemd, `install.sh`, `run_dev.py`,
   dashboard icon, allowed ports, data dir (see [platform.md](platform.md)).
5. **Parity pass** — run the checklist below before calling it done.
6. **Pi update** — use [push-to-pi](../push-to-pi/SKILL.md): package the overlay,
   then give the user `cmd /c "scripts\….cmd -Apply"` (never run scp/ssh yourself).

## Non-negotiables (fixes we keep re-doing)

| Topic | Rule |
|-------|------|
| **Theme accent** | Primary buttons use `var(--accent)` / `var(--accent-ink)`. **Never** override `:root` accent to an app-only colour (e.g. mint green). Shared palettes: default brick, `ocean`, `forest`, `slate` × light/dark — copy token block from NewsCast/Pinboard. |
| **Theme chrome** | Topbar: light / dark / auto (`data-theme-set`). Include `_theme_boot.html` so palette/theme cookies match the portal. Fonts: IBM Plex Sans + Source Serif 4. |
| **Nav** | Always include **Home** → `public_origin` / portal (not app `/`). Phone/tablet &lt;1024: bottom bar, `grid-template-columns: repeat(N, …)` where **N = actual links**. Laptop+ ≥**1024px**: sticky side rail (same as portal — never 800/900). Do not leave Studio’s 4-col comment/grid. |
| **Mobile shell** | `viewport-fit=cover`; `--safe-bottom` / `env(safe-area-inset-bottom)`; `--tap: 44px` (40px ≤559px); phone: `html,body` overflow locked, `.main` scrolls (`-webkit-overflow-scrolling: touch`); stack filters/forms ≤559px; confirm sheet above nav (`margin-bottom: calc(72px + var(--safe-bottom))`). Canonical breakpoints: **720 / 1024 / 1440** — see ux-stonepi. |
| **Icons** | Stroke icons via `_icons.html` macro + CSS `fill: none; stroke: currentColor`. **Settings gear** = shared cog path (Pinboard/Studio), not a sunburst. Settings chips: **icon + `<span>Label</span>`** — General = sliders (`general`/`device`), About = info circle. Nav Settings uses same cog. |
| **Buttons** | Labelled actions use `btn … btn-with-icon` + stroke icon (Save→check, Add→plus, Remove→trash, Refresh→refresh, Search→search, Clear/Cancel→clear). Icon-only controls use `icon-btn` with `aria-label`. No text-only primary/ghost buttons when siblings show icons. |
| **Settings IA** | Product settings + About chips. When SSO: no password/Users/Updates tabs — link to Dashboard. Save = `btn btn-primary btn-with-icon` + check icon. |
| **Confirms** | Shared sheet (`data-confirm-sheet` / `data-confirm`), not bare `confirm()` when shell has the sheet. Logout = **POST** + CSRF. |
| **CSRF** | Forms include `csrf_token`; cookie via `stonepi_auth.csrf` (`CSRF_COOKIE`). |
| **Empty states** | Explain what belongs here + one CTA. |
| **Prefix** | Honor `STONEPI_PREFIX` for redirects and static under nginx path routing. |
| **Copy source** | Clone CSS/HTML chrome from sibling; delete product-only leftovers (Studio preview panes, wrong nav labels). |

## Ship checklist

Copy and tick while building:

```
Shell
- [ ] base.html: viewport-fit=cover, theme-color, _theme_boot, fonts, theme switch, POST logout
- [ ] Confirm sheet present; JS wires data-confirm
- [ ] No app-specific --accent override after palette tokens
- [ ] btn-primary / links / focus rings use var(--accent)

Nav
- [ ] Home → portal origin
- [ ] Column count = link count; labels fit on narrow phones
- [ ] Desktop rail ≥1024px; phone/tablet bottom bar + safe-area (&lt;1024)
- [ ] Every nav item has shared-style stroke icon
- [ ] Labelled buttons use btn-with-icon + matching glyph

Settings
- [ ] Chips with icons (general/device, about, …)
- [ ] About: name, blurb, GitHub, version
- [ ] SSO: no Users/Updates/password ownership
- [ ] Save uses check icon
- [ ] Destructive / secondary actions also iconed (trash, clear, …)
Mobile
- [ ] Offer/list/filter rows wrap ≤559px
- [ ] overflow-wrap on titles/meta
- [ ] Sheet clears bottom nav

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
| Minimal chrome | `apps/pinboard` |
| Full settings / ingest patterns | `apps/newscast` |
| Chat + publish | `apps/studio` |
| Recent full citizen + Pi push | `apps/pricescout` |

## Lessons from siblings (keep these)

Beyond PriceScout chrome fixes, these keep biting new apps:

| Lesson | From | Rule |
|--------|------|------|
| **Host-aware Home** | NewsCast / all | Build Home URL with `portal_home_url(request, …)` so `stonepi.home` stays on that host — never hardcode `.local` only. |
| **`platform_managed`** | NewsCast, FileServe, EventTrakr | When `STONEPI_SESSION_SECRET` is set, hide Users / Updates / password / TLS ownership tabs; link to Dashboard. Solo mode may keep them. |
| **Factory admin banner** | NewsCast, Dashboard | If still `admin`/`admin`, surface an in-UI nudge (settings or dashboard), not docs-only. |
| **Asset cache bust** | NewsCast | Prefer `?v={{ app_version }}-{{ asset_rev }}` (bump `__asset_rev__` on CSS/JS change) over one-off date stamps. |
| **Settings chips wrap** | EventTrakr | `.settings-chips { flex-wrap: wrap; overflow-x: visible }` so About isn’t lost off-screen on narrow phones. NewsCast uses a **grouped hub** on phone/tablet instead — see [chrome.md](chrome.md) Settings chips. |
| **About is a chip with icon** | EventTrakr | Never text-only About; same chip pattern as General. |
| **Capability defaults** | Dashboard Users | New household users get Dashboard only; admins grant app access. Declare real `capabilities` in catalog. |
| **Display scrape privacy** | Pinboard / platform | Public nginx denies `/api/display`; dashboard scrapes via loopback. Don’t expose scrape JSON on the edge. |
| **Confirm sheet copy** | NewsCast / FileServe | Destructive confirms name the consequence (“Remove…”, “Stop…”); OK label matches the verb. |
| **Sign-out POST** | All | Never `<a href="/logout">`. |
| **Studio → FileServe** | Studio | Generated sites: `index.html`, relative assets, phone-first — see `studio-fileserve` skill. |
| **Playwright / scrape apps** | EventTrakr | Browsers install under service user `HOME=/opt/stonepi`, not root’s cache (`install.sh` pattern). |
| **Vault secrets** | Dashboard / apps | Tokens (OpenAI, Salling, webhooks) live in Vault; UI says “configured” vs “not set”, never echo the secret. |
| **Empty + first useful action** | All | First screen after open should show data or a single clear CTA (refresh sources, add feed, pin notice). |

When unsure, open the sibling that already solved it and copy the mechanism — don’t re-invent.
