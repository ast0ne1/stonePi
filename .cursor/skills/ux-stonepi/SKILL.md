---
name: ux-stonepi
description: >-
  Household LAN / multi-app portal UX for StonePi (dashboard, auth, NewsCast,
  FileServe, EventTrakr). Use when designing or reviewing launcher, sidebar,
  settings tabs, empty states, confirms, first-run guidance, SSO vs solo login,
  or shared chrome—not B2B SaaS patterns (Cmd+K, billing, KPI churn metrics).
---

# StonePi household portal UX

StonePi is a few-user LAN portal on a Pi: shared sign-in, home launcher, admin
Users/Updates/Services. Product apps keep their own product settings.

## Apply

- Nav clarity, settings IA, empty states with CTAs, consistent save/confirm,
  time-to-first-useful-action (sign in → see apps → open one).
- Portal owns Users and Updates when `STONEPI_SESSION_SECRET` is set; apps hide
  those tabs and point to the dashboard. Admins manage household accounts on
  **Dashboard → Users**; everyone changes their own password under
  **Dashboard → Settings → General** (not in each app’s General settings when SSO is on).
- Prefer shared confirm sheets (NewsCast/FileServe) over bare `confirm()` where
  the shell already has them.
- Theme/palette: light/dark/auto in header; palette can apply immediately without Save.
- **Settings IA (phone/tablet):** Prefer a **grouped hub → section drill-in** over a long
  horizontal chip scroller when an app has many settings tabs. NewsCast is the
  reference: `/settings` hub with labeled groups; `/settings?tab=` opens a section
  with optional sub-chips for multi-card panels. **Laptop+ (≥1024)** may keep the
  chip bar + stacked cards. Hide Users/Update tabs under SSO as today.

## Do not apply

- Command palette, notification center, billing, signup funnels, activation/churn
  metrics, KPI chart dashboards, hamburger menus on desktop.

## Checklist

1. Empty states explain what belongs here and offer a next step.
2. Destructive actions state consequences (Stop/Restart, delete, restore).
3. Success feedback visible (toast or `banner-ok`), not silent redirects.
4. First-run: factory admin password change is surfaced in-UI, not only in docs.
5. Overview/stats include context (never / age / enabled vs total)—no silent
   fallbacks that look like real counts.
6. Sign-out via POST forms, not GET links.
7. Naming consistent (e.g. Services nav vs page title).
8. Layout matches the **viewport tier model** below (no 800–899 portal/app cliff).

## Viewport tiers (responsive contract)

Canonical CSS breakpoints shared by portal and apps: **720**, **1024**, **1440**.
Do not invent one-off shell thresholds (800 / 860 / 900 / 1100) for sidebar or
main-wide.

| Tier | ID | Typical CSS width | Layout intent |
|------|-----|-------------------|---------------|
| Phone | `phone` | &lt; 720px | Bottom nav (portal + apps), single column, full-width primary actions, `viewport-fit=cover`, `--tap: 44px` (40px ≤559px OK) |
| Tablet | `tablet` | 720px – 1023px | Still **bottom nav** until 1024; 2-col grids where helpful; no hamburger-on-desktop |
| Laptop | `laptop` | 1024px – 1439px | **Persistent side rail** (portal + apps); admin content ~960–1200px (`.main-wide`) |
| Desktop | `desktop` | ≥ 1440px | Wider grids (Home, Services, Health, Users) without stretching prose |

### Shell rules

- **Sidebar / side rail** turns on at **`min-width: 1024px`** everywhere (dashboard
  and product apps). Portrait tablets at 800px stay on bottom nav — same as phone.
- Default `.main` prose column ~720px on phone; from **laptop (≥1024)** `.main` / admin pages **fill the column beside the side rail** (no ~860–1200px island). Settings cards use a 2–3 column panel grid.
- Dense admin grids (3–4 columns) prefer **`min-width: 1440px`** when a third
  column would crowd tablets.
- Touch targets stay ≥44px on phone; laptop+ may tighten non-primary rows only
  where hybrid use is safe.
- Home/Health `data-view-*` density stays **user-controlled** — no forced tier
  defaults.

### Reference QA viewports

| Device | Size |
|--------|------|
| iPhone 13 mini | 375×812 |
| Tablet portrait | 800×1280 |
| Tablet landscape | 1280×800 |
| 14" laptop | 1280×720, 1536×864, and 1920×1200 |
| 24" desktop | 1920×1080 @ 100% |

Living audit notes: [`deploy/responsive-audit.md`](../../../deploy/responsive-audit.md).
Scaffold chrome must match this contract — see [new-stonepi-app](../new-stonepi-app/SKILL.md).
