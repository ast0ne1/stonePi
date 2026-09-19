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
