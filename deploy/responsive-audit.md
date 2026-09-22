# StonePi responsive audit

Canonical breakpoints: **720** (phone/tablet content), **1024** (side rail), **1440** (desktop density).  
See [ux-stonepi](../.cursor/skills/ux-stonepi/SKILL.md#viewport-tiers-responsive-contract).

**Platform cut:** [0.1.6](../CHANGELOG.md) (2026-09-22) ships this contract across portal + apps.

Reference viewports: **375×812**, **800×1280**, **1280×800**, **1280×720**, **1536×864**, **1920×1200** (14"), **1920×1080**.

## Wave 0 — Spec

- [x] Tier model documented in ux-stonepi (incl. 1920×1200 for 14")
- [x] Scaffold skill / chrome.md updated to 1024 side rail
- [x] README links here
- [x] Audit artifact created

---

## Wave 1 — Auth + portal

### Routes

| Surface | Path | CSS | Notes |
|---------|------|:---:|-------|
| Auth login | `/auth/login` | ✓ | `--tap`, safe-area, phone gutters |
| Auth status | `/auth/` hub | ✓ | Same phone tier |
| Home | `/` | ✓ | Bottom nav &lt;1024; list/icons denser ≥1440 |
| Health | `/overview` | ✓ | Stats 2×2 then 4-col ≥1024 |
| Services | `/applications` | ✓ | Auto-fill cards; wider ≥1440 |
| Users | `/users` | ✓ | Access grid 2-col ≥720, 3-col ≥1440 |
| Settings General | `/settings?tab=general` | ✓ | Chips wrap |
| Settings Network | `/settings?tab=network` | ✓ | Long URLs wrap |
| Settings Display | `/settings?tab=display` | ✓ | TRMNL designer 2-col ≥1024 |

### Findings → fixes

| ID | Severity | Finding | Status |
|----|----------|---------|--------|
| W1-P0-1 | P0 | Portal rail 900 vs apps 800 | **Fixed** → 1024 |
| W1-P0-2 | P0 | Health / TRMNL at 900 | **Fixed** → 1024 |
| W1-P1-1 | P1 | Dense grids at 1100 | **Fixed** → 1440 |
| W1-P1-3 | P1 | Health/Settings capped ~1200px with mid-column scrollbar on 1920 | **Fixed** — `.main-wide` fills rail column; Settings multi-col panels |

### Sign-off

- [x] Breakpoint tokens + shell CSS (dashboard **0.1.6**, auth **0.1.6**)
- [ ] Manual QA on reference viewports (operator)

---

## Wave 2 — NewsCast + EventTrakr + FileServe

| App | Version | Paths | Status |
|-----|---------|-------|--------|
| NewsCast | **0.0.3** | Briefing, Sources, Device/Status, Settings, Send, **Saved**, **Search** | Full-width main ≥1024; Settings 2–3 col; story/feed grids 2–3 col; OPDS wrap |
| EventTrakr | **0.0.3** | Calendar, detail, Settings | Full-width main ≥1024; Settings 2–3 col; events denser ≥1440 |
| FileServe | **0.0.0.9** | Library, publish, browse, Settings | Full-width main ≥1024; Settings 2–3 col |

### Sign-off

- [x] Shell CSS updated
- [ ] Manual QA (operator)

---

## Wave 3 — SportGuide + PriceScout + Studio + Pinboard

| App | Version | Paths | Status |
|-----|---------|-------|--------|
| SportGuide | **0.0.2** | Now, Sources, Settings | Full-width main ≥1024 |
| PriceScout | **0.0.2** | Offers, detail, Settings | Full-width main ≥1024; Settings 2-col |
| Studio | **0.0.5** | Create, Projects, editor | Full-width main ≥1024; split height on laptop |
| Pinboard | **0.0.3** | Board, notices/reminders, Settings | Full-width main ≥1024; 2/3-col grids |

### Sign-off

- [x] Shell CSS updated
- [ ] Manual QA (operator)

---

## Wave 4 — Verification

Manual checklist (all waves):

1. No horizontal page scroll at 375, 800, 1280, 1920×1200, 1920×1080 on one primary route per app.
2. Bottom nav at 375 and 800 portrait; **side rail only at ≥1024**.
3. Primary buttons full-width or ≥44px tall on phone.
4. Long URLs / OPDS paths wrap (Network, Device/Status).
5. Dashboard → app at 800px keeps bottom nav (no mid-session cliff).

Optional Playwright: [`scripts/responsive/README.md`](../scripts/responsive/README.md).

### Shell performance (tracked, not layout-blocking)

- Google Fonts on portal `base.html` affect first paint on all tiers including Tailscale remote.
