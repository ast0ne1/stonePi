# StonePi app chrome (UI shell)

**Shell reference: [`apps/newscast`](../../../apps/newscast).** Cross-check with
[`apps/ripple`](../../../apps/ripple) (smallest current shell) and
[`apps/pinboard`](../../../apps/pinboard) (add sheets). Values (colours, type,
radii, spacing) come from the design system in `packages/stonepi_brand`
(`ideation/ui-refresh.md`, F6, platform 0.2.0); never copy hex values into an app.

Living files:

| Piece | Path |
|-------|------|
| Shell | `apps/newscast/app/templates/base.html` (Ripple: `apps/ripple/app/templates/base.html`) |
| Theme boot | `_theme_boot.html` |
| Header app icon | `_brand_mark.html` (`<img>` of the app's fixed icon) |
| UI glyphs | `_icons.html` (`icon(name)`); NewsCast also has `_filter_icon.html` for chips and settings |
| Shared head links | `packages/stonepi_auth/stonepi_auth/templates/stonepi/head.html` (`brand_head`, `brand_tail`) |
| Shared header | `packages/stonepi_auth/stonepi_auth/templates/stonepi/header.html` (`header_search`, `header_tools`, `header_fab`) |
| Tokens / base styles | `packages/stonepi_brand/static/css/tokens.css`, `components.css`, `app-base.css` |
| App CSS / JS | `static/css/app.css`, `static/js/app.js` |
| Settings maps | NewsCast `routers/ui.py` (`SETTINGS_*`); Ripple `routes.py` |

Breakpoints and viewport tiers: [ux-stonepi](../ux-stonepi/SKILL.md);
audit widths: `deploy/responsive-audit.md`.

---

## 1. Page structure (DOM)

```
html[data-stonepi-prefix]            # NewsCast sets it for its JS withPrefix(); optional
  head: meta viewport (viewport-fit=cover) · theme-color · _theme_boot · brand_head · app.css · brand_tail
  body
    .app
      header.topbar
        .brand-row > a.brand (href="/") > _brand_mark + app name
        .topbar-actions
          [status pill / activity strip]
          [header_search(...)]
          [one primary action, e.g. Refresh]
          header_tools(...)          # divider · bell + panel · app switcher (Home on top) · avatar menu
      [.activity-banner]             # optional progress strip under the topbar
      main.main                      # flash banners, then {% block content %}; the only scroller on phones
      nav.nav                        # bottom bar < 1024; side rail ≥ 1024
      [header_fab(...)]              # floating "+", per page (Ripple: {% block fab %})
    .sheet[data-confirm-sheet]       # shared confirm, sibling of .app
    [.sheet[data-sheet=…]]           # page sheets (hoisted to body by JS)
    .toast[data-toast]
    script app.js?v={{ asset_rev }} defer
```

Pages rendered with no `active` (the denied page) hide the nav and fab:
`{% if active %}<nav>…{% endif %}`. `header_tools` is **always** rendered,
signed in or not (decided 2026-10-31; Ripple's `{% if user %}` guard is being
removed).

### `<head>`

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, shrink-to-fit=no" />
<meta name="theme-color" content="#f4efe4" data-theme-color />
<title>{% block title %}MyApp · StonePi{% endblock %}</title>
{% include "_theme_boot.html" %}
{% from "stonepi/head.html" import brand_head, brand_tail %}
{{ brand_head("myapp", fonts_rev) }}
<link rel="stylesheet" href="/static/css/app.css?v={{ asset_rev }}" />
{{ brand_tail(fonts_rev) }}
```

`brand_head` links fonts (`/assets/fonts/fonts.css`), `tokens.css`,
`components.css` and the app's favicon / apple-touch icon from
`/assets/brand/icons/`. `brand_tail` links `app-base.css`, which normalises the
shared classes (`.btn`, `.icon-btn`, inputs, `.card`, `.banner`, `.status-pill`,
`.topbar`, `.nav`, `.sheet-card`, `.brand`) to the design system **after** your
app.css. App.css keeps layout and spacing; more specific selectors still win.

Optional: a `manifest.webmanifest` route (`stonepi_auth.brand.manifest_icons`)
for add-to-home-screen (NewsCast, Pinboard).

---

## 2. Header / topbar

```
[App icon + name]  …  [Search] [Primary action] | [Bell] [App switcher] [Avatar]
```

1. **Brand** — `<a class="brand" href="/">{% include "_brand_mark.html" %}MyApp</a>`.
   Goes to the app's own `/`, never to the portal. `_brand_mark.html`:
   `<img class="brand-mark brand-icon" src="/assets/brand/icons/apps/myapp.svg" width="28" height="28" alt="" />`.
   `app-base.css` sizes it (28px; 40px under 720px, 36px at 420px and below).
2. **Status** (optional) — `.status-pill` / activity strip; hide under 560px when noisy.
3. **Search** (optional) — `{{ header_search("/search", "Search all articles", app="myapp") }}`:
   an icon opening an overlay; submits a GET to the app's own search page.
4. **Primary action** (optional, at most one) — e.g. NewsCast Refresh:
   `form.refresh-form > button.btn.btn-primary.btn-refresh` (icon, `aria-label`
   + `title`). `app-base.css` makes it 40px with a 20px icon, icon-only under 720px.
   Busy state: `.is-busy` + disabled.
5. **`header_tools`** — always last:

   ```jinja
   {% from "stonepi/header.html" import header_search, header_tools, header_fab %}
   {{ header_tools(alerts_bell_state, logout_action="/logout", csrf_token=csrf_token) }}
   ```

   Renders a divider, the **bell** with the app's alerts panel, the **app
   switcher** (Home / Dashboard on top, then the person's apps in their Home
   order) and the **avatar menu** (identity, Appearance light/dark/system,
   Manage account, Log out as a POST form). It carries its own styles and
   script. `alerts_bell_state` comes from
   `bell_context(user, session_cookie=…, home_url=…, enabled=bool(secret), app_id="myapp")`
   (see [platform.md](platform.md) → Notifications). Standalone (no session
   secret) apps pass `local_name=` instead so Log out still shows.

Do **not** add a theme switch, a separate bell, a separate sign-out button or a
Home link to the topbar: `header_tools` owns all of them.

---

## 3. Icons and glyph sizes

Two kinds:

- **App and system icons** — fixed artwork (squircle in the category colour),
  same in both themes: `/assets/brand/icons/apps/{id}.svg` and
  `/assets/brand/icons/system/{id}.svg`, PNGs in `icons/png/`. Used for the
  header brand, switcher, Home cards, favicons. Use `<img>`, never recolour.
- **UI glyphs** — outline SVGs, `viewBox="0 0 24 24"`, `aria-hidden="true"`,
  in `_icons.html` (`{% from "_icons.html" import icon %}{{ icon("plus") }}`).
  `fill: none; stroke: currentColor; stroke-linecap/linejoin: round` in CSS.

Size every glyph in CSS (an unstyled SVG renders at 300×150 in some browsers):

| Context | CSS size | Notes |
|---------|----------|-------|
| Bottom nav / rail | 20×20 | `.nav svg`; active icon in `--accent-text` (app-base) |
| Header primary action | 20×20 | `app-base.css` (`.topbar .btn-refresh svg`) |
| `.icon-btn` | 18×18 | hit area 40–44px |
| `.btn-with-icon` / `.btn > svg` | 18×18 | 15px allowed in dense rows under 560px |
| Settings hub / L2 row | 18 in a 40×40 tile | `.settings-hub-icon` |
| `.section-heading` | ~18 (1.125rem) | |
| `.chip-btn` | 14×14 | |
| Floating "+" | 22×22 | `.sp-fab` (shared) |

Filled dots inside a stroke glyph (info "i", knobs): put
`fill="currentColor" stroke="none"` on the element and restore it in CSS,
otherwise the parent `fill: none` blanks it:

```css
.section-heading svg [fill]:not([fill="none"]),
.chip-btn svg [fill]:not([fill="none"]),
.btn-with-icon svg [fill]:not([fill="none"]) { fill: currentColor; stroke: none; }
```

Settings gear: the shared full cog (copy from NewsCast / Ripple `_icons.html`).

---

## 4. Navigation

- **2–3 primary destinations + Settings last; at most 4 items.** NewsCast:
  Briefing · Saved · Reader · Settings. Ripple: Feed · New post · Settings.
  Pinboard: Board · Notices · Reminders · Settings.
- **No Home item.** Home is the switcher's top entry (since 0.2.0).
- `a.is-active` on the current item (`active` passed from the route).
- Each item: glyph + short label (`<span>`); labels ellipsis rather than wrap.

| Width | Shell |
|-------|-------|
| < 1024 | Bottom bar, `display: grid; grid-template-columns: repeat(N, minmax(0, 1fr))` with **N = number of links**; padding includes `var(--safe-bottom)`; `html, body` overflow hidden, `.main` scrolls |
| ≥ 1024 | `.app` becomes a grid; topbar sticky across; `.nav` a sticky side rail under it (`top: var(--topbar-height)`, one column, row items); page scrolls on `html` |
| ≥ 1440 | Wider content grids only |

Content-only breakpoints: **720** (two-column forms), **560/559** (stack rows),
**420** (smallest header). Never 800/900 for the rail.

Rail width: `var(--app-rail-width)` (200px, `stonepi_brand` tokens), never a number
in the app's CSS. The Dashboard keeps its wider `--sidebar-width` (240px), being the
hub. Decided 2026-10-31; NewsCast's 220px override is being brought into line.

---

## 5. Main page view

- **Hero** — `.hero` with `h1` + optional `.lede` (one sentence). Inline action
  in `.hero-row` when it belongs to the page, not the app.
- **Page status strip** (NewsCast) — `.page-status` with text + optional
  help button opening a `data-sheet="help"`.
- **Filters / chips** — `.chips` + `.chip-btn` (icon + label); long lists get a
  filter sheet (`.chips-with-action` → `data-sheet="filters"`), not endless
  horizontal scroll. Segmented control: `.seg-control > .seg-btn.is-active`.
- **Lists** — simple rows on phones; `.card` for forms and empty states;
  `overflow-wrap: anywhere` on titles and meta.
- **Empty states** — `.card` saying what belongs here + one action.
- **Multi-column** — 1 column on phones → 2 at 1024 → 3 at 1440 for feed /
  card grids. Settings stay one column.
- **Pagination** — Ripple `.pager` (Newer / Older), 20 a page.

---

## 6. Buttons

| Class | Role |
|-------|------|
| `.btn.btn-primary.btn-with-icon` | Primary labelled (Save → check, Add → plus) |
| `.btn.btn-secondary` / `.btn-ghost` | Secondary, Cancel |
| `.btn-danger` | Destructive in a sheet |
| `.icon-btn` + `aria-label` | Icon-only |
| `.chip-btn` | Filters |
| `.sp-fab` via `header_fab` | Secondary create (the floating "+") |

Colours come from `app-base.css` (`--primary` / `--primary-ink`, Sand on dark,
Basalt on light). Red (`--danger`) means destructive or error only. Don't
restyle `.btn-primary` in app.css.

---

## 7. Sheets (input and confirms)

User input lives in **sheets, not inline forms on main pages**. Settings panels
are the exception (their forms live in the panel).

### Add sheets

```jinja
{# base.html or the page's {% block fab %} #}
{{ header_fab("/notes/new", "Add", sheet="add-note") }}
```

```html
<div class="sheet" hidden data-sheet="add-note">
  <form method="post" action="/notes" class="sheet-card card stack sheet-scroll">
    <h2>Add a note</h2>
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
    <label>Note <textarea name="text" required data-autofocus></textarea></label>
    <div class="btn-row">
      <button class="btn btn-primary btn-with-icon" type="submit">{{ icon("check") }} Add</button>
      <button type="button" class="btn btn-ghost" data-close-sheet>Cancel</button>
    </div>
  </form>
</div>
```

- `header_fab(href, label, sheet="name")` renders the 56px floating "+"
  (bottom-right above the bottom bar; label shown from 1024px). `href` is the
  no-JS fallback; with JS, `data-open-sheet="name"` opens the sheet.
- First field `data-autofocus`; Cancel `data-close-sheet`; tapping the backdrop
  or Esc closes.
- **Several kinds of thing to add:** a picker sheet first, each choice opening
  its own sheet (Pinboard `add-pick` → `add-note` / `add-link` /
  `add-reminder` / `add-checklist`, buttons with
  `data-open-sheet="add-note" data-close-sheet`).
- `data-open-on-load` opens a sheet on arrival (Pinboard share target).
- Examples: `apps/pinboard/app/templates/base.html` + `board.html`,
  `apps/newscast/app/templates/_source_sheet.html`, Ripple `feed.html`
  `{% block fab %}`.

### Confirm sheet

One shared sheet in `base.html`; destructive forms opt in:

```html
<form method="post" action="/posts/{{ post.id }}/delete"
      data-confirm="Delete this post?" data-confirm-detail="Its photos go too." data-confirm-ok="Delete">
```

`askConfirm({title, body, okLabel})` fills `[data-confirm-title]`,
`[data-confirm-body]`, `[data-confirm-ok]`. Name the consequence; OK label is
the verb. Never `window.confirm()` (only as the fallback when the sheet is missing).

### Mobile rules

1. Hoist every `.sheet` to `document.body` (`mountSheetsToBody()` on load and on
   open): `position: fixed` inside the scrolling `.main` breaks on iOS.
2. Open → `sheet-open` on `html` and `body` (locks scroll); close removes it
   when no other sheet is open.
3. Below 1024: bottom sheet; padding-bottom ~`76–80px + var(--safe-bottom)` so
   the card clears the bottom bar; the card's inner body scrolls
   (`.sheet-scroll`, `max-height` in `svh`).
4. From 1024: centred modal, no nav clearance.
5. Inputs at least 16px (no iOS zoom).

---

## 8. Settings IA

```
/settings                    → hub: groups of rows (icon tile, title, subtext, chevron)
/settings?tab=…              → the tab's panel, or its L2 list when it has more than one panel
/settings?tab=…&panel=…      → one stacked panel (NewsCast)
```

Same drill-down on every viewport. From 1024 the hub, L2 list and panel sit
in a readable column (`max-width` ~48rem, 56rem from 1440); inputs keep a
`max-width` (~36rem). No chip bar on desktop.

- **Hub row:** `.settings-hub-row` > `.settings-hub-icon` (18-in-40) +
  `.settings-hub-copy` (`.settings-hub-title`, `.settings-hub-sub`) +
  `.settings-hub-chevron`. Groups: `.settings-hub-group` >
  `h2.settings-hub-heading` + `.settings-hub-card`.
- **Back:** `.settings-back` ("← Settings") on every tab / panel.
- **Subtexts** on every row (no "Open this section").
- **Panel headings** use the same icon as the row that opened them.
- **About** last, in an "App" group: name, one-line blurb, GitHub link,
  `Version: {{ app_version }}`.

### Structures

Small app, single panel per tab (Ripple, server-rendered):

```python
SETTINGS_TABS = ("board", "display", "photos", "about")
SETTINGS_TITLES = {"board": "Board", …}
SETTINGS_LEDES = {"board": "The board's name, shown on the feed and the family display.", …}
SETTINGS_HUB_LEDE = "The board, the family display, photos and app details."
SETTINGS_GROUPS = (
    ("board", "Board", (
        ("board", "Board", "Name of the family board.", "board"),   # (tab, title, subtext, icon)
        …
    )),
    ("app", "App", (("about", "About", "Version and project links.", "about"),)),
)
```

Tabs with several panels (NewsCast, JS drill-down with server fallback):

| Map | Purpose |
|-----|---------|
| `SETTINGS_TABS` | `(key, label)` in order |
| `SETTINGS_LEDES` | Lede under the tab title |
| `SETTINGS_HUB_SUBTEXTS` | Hub row subtexts |
| `SETTINGS_HUB_LEDE` | Hub lede |
| `SETTINGS_GROUPS` | `(group_id, label, (tab keys…))` |
| `SETTINGS_SECTION_PANELS` | tab → `((panel_id, label, (card ids…)), …)`; single-panel tabs omitted |
| `SETTINGS_PANEL_ICONS` / `SETTINGS_PANEL_SUBTEXTS` | L2 row icon and subtext per panel |
| `ADMIN_ONLY_SETTINGS_TABS` | Hidden from members |
| `PLATFORM_HIDDEN_SETTINGS_TABS` | NewsCast's solo-mode Users / Update; a new app has neither |

### Who sees what

- **Household settings are admin-only**, enforced on the POST
  (`if not _is_admin(user): return _form_error(back, "Only an admin can …")`).
  In the page, members **don't see household settings at all**: hide those tabs,
  panels and hub rows. Members see only their own (personal) settings, and only
  where the app has some; an app with none shows members just About (and
  Notifications if it emits events). Decided 2026-10-31.
- **Personal settings** (per person) are keyed by the Auth user id.
- **Notifications** tab only for apps that emit events: the shared
  `notifications_card` plus the app's own timing settings, nothing else.
- No Users, Updates, password, TLS or backup tabs: the Dashboard owns them.

---

## 9. Flash messages, toasts and banners

NewsCast:

- **Done-messages are a toast.** `.toast[data-toast]` in `base.html`
  (`role="status" aria-live="polite"`). POST forms are submitted by JS
  (`bindPostForms`; `data-native` opts out): the route answers JSON
  `{"ok": true, "message": "Saved"}` when the request has
  `X-Requested-With: fetch` / `Accept: application/json`; the script stores the
  message (`toastAfterReload`), reloads, and the toast fades after 4.5s. Phone:
  centred above the bottom bar; from 1024: top-right under the topbar.
- **Errors stay visible.** A failed JS submit answers `{"ok": false, "message": …}`
  with a 4xx: the message goes into the form's `<p class="error-text" hidden data-form-error>`
  and an error toast. Server-rendered errors (login) are a `.banner`.
- **`.banner`** (error), `.banner-ok`, `.banner-muted` (standing notices: factory
  admin password, setup nudges) at the top of `main`.

The rule (decided 2026-10-31; Ripple's `.banner-ok` messages and
`x-ripple-ajax` replies are being brought into line):

- **Done → toast.** Never a `.banner-ok` for "Saved"-type messages. A form that
  posts natively and redirects passes `?msg=`; `base.html` puts it on the toast
  (`data-toast-message`) and the script removes `msg` / `err` from the address
  (`history.replaceState`) so a reload or Back doesn't repeat it.
- **Error → inline next to the form**, or a `.banner` after a redirect.
- **Background form posts** send `X-Requested-With: fetch` and get JSON back:
  `{"ok": true, "message": "Saved"}`, plus `"redirect": "/path"` when the page
  should move on; on failure a 4xx with `{"ok": false, "message": "…"}`. No
  app-specific headers.
- Never `alert()`.

---

## 10. Theme and colour

- `_theme_boot.html` runs before any CSS: reads `stonepi-theme`
  (`light` / `dark` / `system`, cookie + localStorage), sets
  `html[data-theme]`, `data-theme-pref` and `color-scheme`. Copy it from
  Ripple / NewsCast as is (the legacy cookie names in it are harmless).
- The choice is made in the avatar menu (Appearance) or Dashboard → Settings →
  General → Appearance. Apps never show their own switch.
- **Colour palettes (default / ocean / forest / slate) and per-app accent
  colours were removed in 0.2.0.** Don't reintroduce them.
- Use the role tokens (`--canvas`, `--surface`, `--surface-raised`, `--text`,
  `--text-muted`, `--border`, `--primary`, `--link`, `--danger`, `--success`,
  `--tint-*`). Older names (`--paper`, `--ink`, `--muted`, `--line`, `--card`,
  `--accent`, `--ok`, `--error`, `--serif`, `--chrome`) are aliases in
  `tokens.css`; fine in copied CSS, prefer roles in new CSS.
- App.css `:root` defines layout variables only: `--gutter`, `--tap`
  (44px), `--safe-bottom: env(safe-area-inset-bottom, 0px)`, `--topbar-height`.
- Fonts: Sora (display, titles, app name), IBM Plex Sans (body), IBM Plex Mono
  (numbers, hosts), served from `/assets/fonts` (no Google Fonts).

---

## 11. Cache busting

Automatic. `app/__init__.py`:

```python
from pathlib import Path

from stonepi_auth.brand import asset_rev

__version__ = "0.0.11"
__asset_rev__ = asset_rev(Path(__file__).resolve().parent / "static")  # cache-bust token; changes with static/
__github_user__ = "ast0ne1"
__github__ = "https://github.com/ast0ne1"
```

Where the Jinja env is created:

```python
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
```

Templates: `app.css?v={{ asset_rev }}`, `app.js?v={{ asset_rev }}`; fonts and
brand CSS take `fonts_rev` through `brand_head` / `brand_tail`. Never type a
date or number after `?v=`: `packages/stonepi_auth/tests/test_platform_consistency.py`
fails on it. (NewsCast and some older apps use `?v={{ app_version }}-{{ asset_rev }}`;
both pass.)

---

## 12. JS checklist (`static/js/app.js`)

Copy from NewsCast / Ripple and keep:

- Theme **apply** (read `stonepi-theme`; follow system changes); no setters.
- `askConfirm` + `form[data-confirm]` wiring.
- `mountSheetsToBody`, `openSheetEl` / `closeSheetEl`, `[data-open-sheet]`,
  `[data-close-sheet]`, backdrop / Esc close, `data-autofocus`, `sheet-open`.
- Toast (`toast`, `toastAfterReload`) and `bindPostForms` with `data-native` opt-out.
- Prefix-aware `fetch` for JSON calls (NewsCast `withPrefix` reads
  `html[data-stonepi-prefix]`; the prefix middleware also rewrites literal
  `fetch("/…")` and `send("/…")` calls in served JS).
- Settings drill-down (only if the app has multi-panel tabs).

---

## 13. Phone vs laptop cheat sheet

| Concern | Phone / tablet (< 1024) | Laptop+ (≥ 1024) |
|---------|-------------------------|------------------|
| Nav | Bottom bar + safe-area | Side rail |
| Scroll | `.main` only | Document |
| Header | App icon 40/36px, primary action icon-only < 720 | Icon 28px, labels |
| Add | Floating "+" above the bar | Floating "+ Label" bottom-right |
| Sheets | Bottom sheet, nav clearance, hoisted | Centred modal |
| Settings | Hub → L2 → panel | Same, readable column |
| Toast | Above the bottom bar | Top-right under the topbar |

---

## 14. Chrome parity checklist

```
- [ ] head: viewport-fit=cover, theme-color, _theme_boot, brand_head, app.css?v={{ asset_rev }}, brand_tail
- [ ] Topbar: brand → / · [header_search] · [one primary action] · header_tools(…, csrf_token=csrf_token)
- [ ] No theme switch, no own bell/logout, no Home link
- [ ] Nav: no Home; Settings last; ≤ 4; N columns = N links; rail ≥ 1024
- [ ] Glyphs sized in CSS; filled-pip restore rules
- [ ] Add flows: header_fab → sheet; picker sheet for several kinds; data-autofocus; data-close-sheet
- [ ] Confirm sheet + form[data-confirm]; sheets hoisted; clear the bottom bar
- [ ] Toast for done, inline/banner for errors
- [ ] Settings: hub → (L2) → one panel; subtexts; back link; About last
- [ ] No colour tokens or hex in app.css; no palettes
- [ ] Empty states + overflow-wrap; inputs ≥ 16px
```
