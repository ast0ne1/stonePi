# StonePi app chrome (theme, layout, icons)

Canonical copy sources: `apps/pinboard` or `apps/newscast` `base.html`,
`static/css/app.css` token block, `_theme_boot.html`, `_icons.html`.

## Theme & colour

### Tokens (required)

Define on `:root` / `[data-theme="light"]` and `[data-theme="dark"]`:

`--paper` `--ink` `--muted` `--line` `--card` `--accent` `--accent-ink`
`--ok` `--error` `--wash` `--gutter` `--radius` `--tap` `--chrome` `--serif`
`--theme-meta` `--topbar-height` `--safe-bottom`

Plus palette overrides:

- `[data-palette="ocean"][data-theme="light|dark"]`
- `[data-palette="forest"][data-theme="light|dark"]`
- `[data-palette="slate"][data-theme="light|dark"]`

**Default accent** is brick/terracotta (`#8b1e1e` / `#e06a5a`), not green.
Forest palette already owns green. Do not append a second block that reassigns
`:root --accent` for “app identity.”

### Behaviour

- Topbar theme switch: `data-theme-set="light|dark|system"`.
- `_theme_boot.html` reads/writes **shared** cookies/localStorage keys so
  portal and apps stay in sync (copy from sibling; do not invent new key names).
- Palette may apply immediately (dashboard pattern); product Save is for
  product fields only.
- `meta theme-color` + `[data-theme-color]` updated by theme JS when present.

### Typography

- UI: `"IBM Plex Sans", "Segoe UI", system-ui, sans-serif` → `--chrome`
- Titles: `"Source Serif 4", Georgia, serif` → `--serif`
- Load both from Google Fonts in `base.html` (same URLs as siblings).

### Buttons & links

```css
.btn-primary { background: var(--accent); color: var(--accent-ink); }
.text-link { color: var(--accent); }
```

**Every labelled button/link that looks like a button gets an icon:**

```html
<button class="btn btn-primary btn-with-icon" type="submit">{{ icon("check") }} Save</button>
<a class="btn btn-ghost btn-with-icon" href="…">{{ icon("clear") }} Clear</a>
```

| Action | Icon name |
|--------|-----------|
| Save / Continue / Enable | `check` |
| Add / Create | `plus` |
| Remove / Delete / Clear history | `trash` |
| Search | `search` |
| Clear / Cancel | `clear` |
| Refresh | `refresh` |
| Send / Pin | `pin` or `send` |

Icon-only controls: `icon-btn` + `aria-label` / `title` (theme, sign-out). Do not ship text-only `.btn` when Pinboard/NewsCast show icons for the same kind of action.

Import the macro in each template that uses it: `{% from "_icons.html" import icon %}`.

## App shell layout

### DOM

```
.app
  header.topbar (brand + theme + sign-out)
  main.main
  nav.nav
confirm sheet (sibling of .app)
```

### Phone (< 800px)

- `.app`: column flex, `height: 100dvh`, overflow hidden.
- `.main`: flex 1, `overflow-y: auto`, `-webkit-overflow-scrolling: touch`,
  `min-width: 0`, `overflow-x: hidden`.
- `.nav`: bottom, `padding-bottom: calc(10px + var(--safe-bottom))`,
  `grid-template-columns: repeat(N, minmax(0, 1fr))` — **N = number of `<a>`**.
- Nav links: column flex, icon 20px, label ~0.55–0.62rem, `min-height: var(--tap)`.
- ≤559px: stack filter/search forms; wrap dense rows; `--tap: 40px` optional.
- Confirm `.sheet-card`: margin above nav so actions aren’t covered.

### Desktop (≥ 800px)

- `.app`: CSS grid — nav column ~200px, main fluid.
- Nav: sticky side rail, row flex links (icon + label), border-right.
- `html, body`: restore page scroll (`overflow: auto`); do not leave phone lock.

### Viewport / PWA-ish

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, shrink-to-fit=no" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-title" content="AppName" />
```

## Navigation IA

1. **Home** — portal launcher (`public_origin` / `stonepi_home_url`), not app root.
2. Primary product screens (Offers, Board, Briefing, …).
3. Secondary ops if needed (Sources, Feeds).
4. **Settings** — last.

Active state: `class="is-active"` on current destination. Keep destinations
few enough that six icons still fit a phone; if more, match NewsCast’s denser
label sizing — do not horizontal-scroll a broken 4-col grid.

## Icons

### Macro

`{% from "_icons.html" import icon %}` then `{{ icon("settings") }}`.

SVG: `viewBox="0 0 24 24"`, paths only; stroke applied in CSS:

```css
.nav svg, .chip-btn svg {
  fill: none; stroke: currentColor; stroke-width: 1.7;
  stroke-linecap: round; stroke-linejoin: round;
}
```

Exceptions: small filled dots (About “i” hub) may use `fill="currentColor" stroke="none"`.

### Required shared glyphs

| Name | Use | Notes |
|------|-----|--------|
| `home` | Nav Home | House |
| `settings` | Nav Settings | **Full cog** (Pinboard/Studio path), not ray-burst |
| `general` / `device` | Settings chip | Sliders |
| `about` | Settings chip | Circle + i |
| `check` | Save / Continue / Enable | |
| `plus` | Add | |
| `trash` | Remove / clear history | |
| `refresh` | Refresh | |
| `clear` | Clear / Cancel | |
| `pin` / `send` | Cross-app send | |
| Product icons | Nav destinations | Match metaphor; keep stroke weight |

Dashboard launcher needs a matching glyph in
`apps/dashboard/app/templates/_icons.html` and catalog `"icon": "{id}"`.

### Settings chips

```html
<a class="chip-btn{% if settings_tab == 'general' %} is-active{% endif %}" …>
  {{ icon("general") }}<span>General</span>
</a>
```

Never text-only chips when siblings show icons. Active chip: ink fill / paper text
(sibling `.chip-btn.is-active` rules).

## Settings content

- **General (or product tabs):** only prefs this app owns (e.g. currency, zip).
- **About:** `app_name`, one-line blurb, GitHub link, `app_version`, optional
  integration status.
- Under SSO: point password / household users to Dashboard; do not duplicate.

## Auth & forms

- `_require_user` + `stonepi_auth` session cookie; capability checks via catalog.
- CSRF on every POST; `csrf_ok` / `set_csrf_cookie`.
- Sign-out: `<form method="post" action="/logout">`.
- Flash: `.banner` / `.banner-ok` in `main`.

## Content patterns

- Hero: `h1` (serif) + `.lede`.
- Cards: use sparingly; OK for settings forms / empty states.
- Empty state: title + hint + primary CTA button/link.
- Lists: prefer simple rows over heavy card grids on mobile.
- `overflow-wrap: anywhere` on user-facing titles and meta.

## JS

Copy sibling `static/js/app.js` theme + confirm helpers. Keep `data-native` on
forms that must full-navigate. Bump `?v=` on CSS/JS when shipping visual fixes.
