# StonePi app chrome (NewsCast baseline)

**Canonical chrome source: [`apps/newscast`](../../../apps/newscast).**  
Pinboard is fine for a *minimal* shell; any app with multi-tab settings, sheets,
topbar refresh, or dense mobile IA should copy NewsCast mechanisms — do not invent.

Living files:

| Piece | Path |
|-------|------|
| Shell | `apps/newscast/app/templates/base.html` |
| Theme boot | `_theme_boot.html`, `_brand_mark.html` |
| Action icons | `_icons.html` (`icon()`) |
| Nav / chip / settings icons | `_filter_icon.html` (`filter_icon()`, `filter_chip()`) |
| CSS | `static/css/app.css` |
| JS | `static/js/app.js` |
| Settings maps | `routers/ui.py` (`SETTINGS_*`) |
| Cache bust | `app/__init__.py` → `__asset_rev__ = asset_rev(...)` (automatic) |

Also read [ux-stonepi](../ux-stonepi/SKILL.md) for portal IA and the **720 / 1024 / 1440** contract.

---

## 1. Overall structure (DOM)

```
.app
  header.topbar
    .brand-row > a.brand (+ brand-mark)
    .topbar-actions
      [.status-pill] · [.refresh-form] · a.icon-btn.alerts-bell · form.logout > .icon-btn.sign-out
      # light/dark/auto (.theme-switch) is portal (dashboard) only — do not add here
  [.activity-banner]          # optional progress strip under topbar
  main.main                   # ONLY scroller on phone
  nav.nav                     # bottom bar <1024; side rail ≥1024
.sheet[data-confirm-sheet]    # sibling of .app (hoisted to body in JS)
[+ page-local .sheet[data-sheet=…]]
```

Do not put sheets inside scrolling `.main` without hoist — `position:fixed` breaks on iOS.

---

## 2. Header / topbar

### Order (left → right)

1. **Brand** — mark + product name → app home (`/`), not portal Home.
2. Optional status / activity pill (hide ≤420px when noisy).
3. **Global Refresh** (when the app has a global sync) — `btn btn-primary btn-refresh` before the bell.
4. **Bell** — shared `alerts_bell` macro (`stonepi/alerts.html`), links to the Dashboard Notifications page; dot until personal alerts are set up. Signed-in under the platform only. See [platform.md](platform.md) → Notifications.
5. **Sign out** — POST `/logout`, `icon-btn.sign-out`, `aria-label` + `title`.

**Light / dark / auto** lives only on the **dashboard** header (`.theme-switch` / `data-theme-set`). Product apps must not reintroduce it — they apply the shared `stonepi-theme` cookie via `_theme_boot.html`.

### Global Refresh (NewsCast pattern)

```html
<form method="post" action="/ingest" class="refresh-form">
  <button class="btn btn-primary btn-refresh" type="submit" data-refresh>
    <svg …><!-- refresh arrow --></svg>
    <span>Refresh</span>
  </button>
</form>
```

- Place **next to logout**.
- Busy state: `.is-busy` + disabled; spin the SVG.
- **≤420px:** hide the `<span>` → icon-only primary (keep tap target).
- Apps without a global sync may omit Refresh.

### Brand mark sizing

- `.brand-mark`: **22×22**, `stroke-width: 1.6`, `fill: none`, `stroke: currentColor`.
- Brand text: `--serif`, ~1.35rem, weight 600.

### Light/dark buttons (dashboard only)

- Hit: **36×36**; SVG **16×16**, stroke **1.8**.
- Active: `.is-active` → ink fill / paper stroke on glyphs.
- Product apps: omit this control; still boot shared prefs.

---

## 3. SVG sizing matrix (do not invent larger defaults)

All UI glyphs use `viewBox="0 0 24 24"`. Size is **CSS**, not width/height on the SVG path file. Cap sizes so icons never “blow up” and need one-off fixes.

| Context | CSS target | Stroke | Notes |
|---------|------------|--------|-------|
| Brand mark | 22×22 | 1.6 | `_brand_mark.html` |
| Bottom / side nav | 22×22 | 1.7 | `.nav svg` |
| Theme switch (dashboard only) | 16×16 | 1.8 | Not in product-app topbars |
| Refresh + sign-out | 18×18 | 1.8 | `.btn-refresh svg`, `.sign-out svg` |
| Labelled `.btn-with-icon` | 18×18 | 2 | 15×15 OK ≤559px / dense CTAs |
| `.icon-btn` | 18×18 | 1.7 | Hit area = `--tap` (44 / 40) |
| `.chip-btn` / settings chips | 14×14 | 1.7 | |
| `.chips-action` / page-status help | 16–18 in 32–36 circle | 1.7 | |
| `.section-heading` | ~1.125rem (≈18) | 1.7 | Accent-colored |
| Settings hub / L2 row icon tile | glyph **18×18** in **40×40** tile | 1.7 | `.settings-hub-icon` |
| Fav / story actions | 22×22 | 1.6 | `.fav-btn` |
| Feed row favicon | 40×40 image | — | Not a stroke icon |

**Rules**

- Prefer **18×18** for chrome actions; **22×22** only for nav and large tap glyphs.
- Never ship bare SVGs without a CSS width/height rule for that parent (they render at intrinsic 300×150 in some browsers).
- Stroke icons: `fill: none; stroke: currentColor; stroke-linecap/join: round`.
- Intentional filled dots (info “i”, slider knobs): `fill="currentColor" stroke="none"` on the path **and** restore with:

```css
.section-heading svg [fill]:not([fill="none"]),
.chip-btn svg [fill]:not([fill="none"]),
/* …same for .btn-with-icon, .chips-action, .page-status-action, .seg-btn */
{ fill: currentColor; stroke: none; }
```

Otherwise parent `fill: none` blanks the pip.

### Two icon macros

| Macro | File | Use |
|-------|------|-----|
| `icon("check")` | `_icons.html` | Form actions: save, trash, plus, search, publish, … |
| `filter_icon("device")` | `_filter_icon.html` | Nav metaphors, settings chips, category filters, `open`, `sliders`, `info` |

Do not duplicate divergent cog paths — Settings nav/chip use the **shared full cog**.

---

## 4. Navigation

1. **Home** → portal (`stonepi_home_url` / `portal_home_url`) — first nav item.
2. Primary product screens.
3. Secondary ops if needed.
4. **Settings** — last.

- Active: `a.is-active`.
- `grid-template-columns: repeat(N, minmax(0, 1fr))` where **N = number of links**.
- Phone labels ~0.7rem; denser if 6–7 items (NewsCast). Do not horizontal-scroll a broken 4-col grid.

### Breakpoints (shell)

| Width | Shell |
|-------|--------|
| &lt; 1024 | Bottom nav + safe-area; `html,body` overflow locked; `.main` scrolls |
| ≥ **1024** | Sticky side rail (~200px); page scroll on `html,body` |
| ≥ **1440** | Wider multi-column content grids |

Content-only: **720** (2-col forms), **559/560** (stack), **420** (icon-only refresh). Do not invent 800/900 for the rail.

---

## 5. Main page view

### Hero

- `.hero` / `.hero-row` / `.hero-row-inline` — `h1` (serif) + optional primary action.
- `.lede` for one supporting sentence.
- Dual labels when space is tight: `.btn-label-full` / `.btn-label-short` (short ≤559px).

### Page status strip

```html
<div class="page-status">
  <span class="page-status-dot …"></span>  <!-- optional -->
  <p class="page-status-text">…</p>
  <button class="page-status-action" type="button" data-open-sheet="help" …>
    {{ filter_icon("info") }}
  </button>
</div>
```

- Help button: hide on laptop+ if the same copy lives in settings desktop chrome.
- Keep strip one line; overflow ellipsis on text.

### Filters / chips

- Horizontal `.chips` + `.chip-btn` / `filter_chip()`.
- Long lists: `.chips-with-action` = sticky chips + `.chips-action` opens a **filter sheet** (do not rely on endless horizontal scroll alone).
- Segmented day/tabs: `.seg-control` > `.seg-btn.is-active`.

### Cards / lists

- Prefer simple rows on mobile; `.card` for settings forms and empty states.
- Story / content expand: collapsed summary hidden until `.is-expanded` (NewsCast briefing).
- Expand affordance: chevron control and/or muted “Tap for …” — not a mystery whole-card hit with zero cue.
- `overflow-wrap: anywhere` on titles and meta.
- Empty: `.empty.card` — what belongs here + one CTA.

### Multi-column content

- 1 col phone → **2 @1024** → **3 @1440** for story/feed grids. Settings stay one stacked panel.

---

## 6. Buttons

| Class | Role |
|-------|------|
| `.btn.btn-primary.btn-with-icon` | Primary labelled (Save, Refresh, Generate) |
| `.btn.btn-secondary` / `.btn-ghost` | Secondary |
| `.icon-btn` | Icon-only destructive-leaning (delete); needs `aria-label` |
| `.icon-btn-quiet` | Icon-only non-destructive (row refresh, eye) — no error hover |
| `.chip-btn` | Filters / settings tabs |
| `.toggle` | Enable/disable |

Every labelled button that siblings icon gets `btn-with-icon` + glyph (Save→check, Add→plus, Remove→trash, Search→search, Clear→clear, Refresh→refresh).

---

## 7. Panels & grouping

- `.card.stack` — settings / form blocks.
- `.section-heading` — icon + title on each settings card (match L2 icon).
- `.section-label` — uppercase group label above hub / feed groups.
- `.group-card` — list container (hub rows, feed rows) without nested “card in card” noise.
- Feed / source rows: `<details>` summary + panel; trailing chevron; health/meta line.

---

## 8. Popups / sheets

### Types

1. **Confirm** — `data-confirm-sheet` in `base.html`; forms: `data-confirm`, `data-confirm-detail`, `data-confirm-ok`.
2. **Filter / picker** — `data-sheet="filters"` + `data-filter-pick`.
3. **Help / info** — `data-sheet="help"` (+ `.help-sheet-body` scroll region).

### Mobile non-negotiables

1. **`mountSheetsToBody()`** — hoist every `.sheet` to `document.body` on load and again on open.
2. Open → add `sheet-open` on `html` and `body` (lock overflow).
3. Bottom padding / sheet clearance: ~`76–80px + var(--safe-bottom)` so the card clears the bottom nav.
4. **Inner** body scrolls (`.help-sheet-body`, `.filter-picker`, form stacks) — not the whole viewport behind the sheet.
5. Desktop ≥1024: centered modal, no nav clearance padding.
6. Prefer sheets over `window.confirm()`. Destructive OK label matches the verb (“Remove”, “Stop”).

---

## 9. Settings IA (NewsCast reference)

### Every viewport

```
/settings                    → grouped HUB
/settings?tab=…              → L2 panel list (multi-card tabs) OR single panel
/settings?tab=…&panel=…      → one stacked card (others .is-panel-hidden)
```

1. **Hub** — `SETTINGS_GROUPS`: section label + `.settings-hub-row` (icon tile, title, **subtext**, chevron).
2. **L2 list** — from `SETTINGS_SECTION_PANELS` when a tab has more than one card; each row uses `SETTINGS_PANEL_ICONS` + **`SETTINGS_PANEL_SUBTEXTS`** (never leave “Open this section”).
3. **Cards** — `.card.stack[data-settings-panel-card]`; heading icon matches L2. One panel at a time, stacked (not a 2–3 column mosaic).
4. Back: `.settings-back` → hub or L2 (`data-settings-hub-back`).

Icon bank (hidden): `[data-settings-panel-icon-bank]` + `[data-settings-panel-icon="tab-panelId"]` for JS-built L2 rows.

### Laptop+ (≥1024) layout only

Same drill-down as the phone. Do not switch to a chip bar.

- Hub groups: **one column** (uneven You/House/System-style groups leave gaps in two columns).
- L2 list and the open panel: a readable column (`max-width` about 48rem, 56rem from 1440).
- Keep input `max-width` so fields do not stretch edge to edge.

### Maps to define in the router

| Map | Purpose |
|-----|---------|
| `SETTINGS_TABS` / ledes | Chip labels + hero copy |
| `SETTINGS_GROUPS` | Hub grouping |
| `SETTINGS_HUB_SUBTEXTS` | Hub row blurbs |
| `SETTINGS_SECTION_PANELS` | L2 panels → card ids |
| `SETTINGS_PANEL_ICONS` | L2 / heading icon names |
| `SETTINGS_PANEL_SUBTEXTS` | L2 blurbs |
| `PLATFORM_HIDDEN_*` / admin-only | SSO vs solo |

### View options pattern

Product chrome prefs that are **not** EPUB/export settings live under General → **View options** (e.g. Briefing open button + label). Keep export/layout flags under Publication / X3.

### SSO

When `STONEPI_SESSION_SECRET` is set: hide Users / Updates / password ownership; link to Dashboard. Solo may keep them.

---

## 10. Theme & colour

### Tokens

`--paper` `--ink` `--muted` `--line` `--card` `--accent` `--accent-ink`
`--ok` `--error` `--wash` `--gutter` `--radius` `--tap` `--chrome` `--serif`
`--theme-meta` `--topbar-height` (~65px) `--safe-bottom`

Palettes: `default` | `ocean` | `forest` | `slate` × light/dark.  
**Never** override `:root --accent` for app identity (default = brick; forest owns green).

### Behaviour

- `_theme_boot.html` + shared cookies (`stonepi-theme` = light/dark/auto, `stonepi-palette` = colour palette).
- **Light / dark / auto:** set only on the **dashboard** header; product apps consume the shared pref (no topbar toggle).
- **Colour palette:** portal Settings → Appearance only; apps apply via boot.
- Fonts: IBM Plex Sans + Source Serif 4 (same Google Fonts URLs as siblings).
- Inputs: `font-size: 16px` minimum on phone (prevent iOS zoom).

```css
.btn-primary { background: var(--accent); color: var(--accent-ink); }
.text-link { color: var(--accent); }
```

---

## 11. Auth & forms

- Session via `stonepi_auth`; CSRF on every POST.
- Sign-out: `<form method="post" action="/logout" data-native>`.
- Flash: `.banner` / `.banner-ok` / `.banner-muted` in `main`.
- Factory admin nudge in-UI when still `admin`/`admin`.

---

## 12. Asset cache bust

Automatic — nothing to bump by hand. The token is a hash of `app/static/`, so any CSS/JS change (edit or update overlay) busts the cache on the next restart.

```python
# app/__init__.py
from pathlib import Path

from stonepi_auth.brand import asset_rev

__version__ = "0.0.6"  # user apps: the shared user-app version; see Versioning below
__asset_rev__ = asset_rev(Path(__file__).resolve().parent / "static")  # cache-bust token; changes with static/
```

```python
# where the Jinja env is created (FastAPI shown; Flask: app.jinja_env.globals.update(...))
from stonepi_auth.brand import fonts_rev
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
```

```html
<link rel="stylesheet" href="/assets/fonts/fonts.css?v={{ fonts_rev }}" />
<link rel="stylesheet" href="/static/css/app.css?v={{ app_version }}-{{ asset_rev }}" />
<script src="/static/js/app.js?v={{ app_version }}-{{ asset_rev }}" defer></script>
```

Never type a date or number after `?v=` — `packages/stonepi_auth/tests/test_platform_consistency.py` fails on it.

### Versioning

- `__version__` in `app/__init__.py` is the only place an app's version lives. Dashboard Health / Services / Updates, the app's own Settings "Version:" line, release zips and the CHANGELOG versions table all read it.
- System apps (Dashboard, Auth, Notify, Recover) carry the platform version (`VERSION`, e.g. `0.1.8`), then `0.1.8.N` for patches between platform cuts.
- User apps were aligned to `0.0.6` at the 0.1.8 cut. From there each moves **independently**: an app bumps its own patch (e.g. NewsCast `0.0.7`) when it changes, and others stay put. Three-part only, no four-part versions. A new user app starts at `0.0.6`.
- Register the app in `APP_CATALOG` (`packages/stonepi_auth/stonepi_auth/catalog.py`): Updates, release zips and icons follow it automatically.

---

## 13. JS chrome checklist

Copy from NewsCast `app.js` and keep:

- Light/dark + palette **apply** (read shared cookies; no product-app light/dark click setters)
- Confirm sheet + `data-confirm*`
- `mountSheetsToBody` + `sheet-open`
- Settings hub / L2 on every viewport (no desktop chip-bar branch)
- `data-native` on forms that must full-page navigate
- Toast positioning: above bottom nav on phone; under topbar on desktop

---

## 14. Desktop vs mobile cheat sheet

| Concern | Mobile (&lt;1024) | Desktop (≥1024) |
|---------|-------------------|-----------------|
| Nav | Bottom bar + safe-area | Side rail 200px |
| Scroll | `.main` only | Document scroll |
| Settings | Hub → L2 → one stacked card | Same drill-down; hub in 2 columns |
| Sheets | Bottom sheet, nav clearance, body hoist | Centered modal |
| Refresh | Icon-only ≤420px | Label + icon |
| Story cards | No left accent rail; expand for body | Optional multi-col grid |
| Help on status | Visible | Often hidden |

---

## 15. Scaffold parity checklist (chrome)

```
- [ ] base.html: viewport-fit=cover, theme-color, _theme_boot, fonts (no light/dark switch)
- [ ] Topbar: brand → [Refresh] → POST logout
- [ ] SVG sizes match matrix (nav 22, actions 18, chips 14, hub tile 18-in-40)
- [ ] fill:none stroke icons + filled-pip restore rules
- [ ] Home → portal; nav column count = link count; rail ≥1024
- [ ] Confirm sheet + mountSheetsToBody + safe-area clearance
- [ ] Settings: hub → L2 → one stacked panel on every viewport; one-column hub from 1024; SSO hides ownership
- [ ] section-heading icons match L2 icons
- [ ] Empty states + overflow-wrap
- [ ] `asset_rev` / `fonts_rev` globals wired on CSS/JS/fonts (no literal `?v=`)
- [ ] No app-specific --accent override
```
