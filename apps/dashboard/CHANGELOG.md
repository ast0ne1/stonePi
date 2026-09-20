# Changelog — Dashboard

## 0.1.4 — 2026-09-20

### Changed
- Nav label **Health** (route still `/overview`): monitor copy; Apps responding stat; Version + URL meta; request-host display URLs
- **Services**: manage-only copy; Route + Port meta; no Healthy pill / Status line
- Settings General uses the sliders icon; About cards flush with shared portal chrome

## 0.1.3 — 2026-09-20

### Changed
- Overview absorbs **Settings → Watch**: alert banner when not healthy, disk + backup stats, one click-to-launch grid; `?tab=watch` redirects to Overview
- Services: per-card Enabled toggle autosaves (confirm when hiding); Dashboard and Auth stay always on
- Users: Accounts list first (rows collapsed); Add account expands the form; new users default to Dashboard only
- Version aligned to platform **0.1.3**

## 0.1.2 — 2026-09-19

### Changed
- **Users** nav for admins (create / apps / roles)
- Every signed-in person changes **their own password** under Settings → General → Your password
- Version aligned to platform **0.1.2** (was incorrectly 0.0.3)

### Fixed
- Overview **Last backup** respects `STATUS=failed` / `skipped` (no longer looks OK when only a timestamp exists)
- Sign out / login on path installs use `/auth` even when `AUTH_URL` is loopback (no more `127.0.0.1:8011` on `stonepi.home`)
- **Settings → General → Appearance** for every signed-in household member (not admin-only); other Settings tabs stay admin
- Theme/palette persist via cookies + localStorage so prefs survive logout and follow the hostname
- Home launcher / Services cards use **relative** paths on path installs so `stonepi.home` does not jump to `stonepi.local`
- Settings import of `__author__` / `__github__` no longer 500s the page

## 0.0.2 — 2026-09-19

### Added
- **Display → Status wall** fixed design: header, CPU/RAM/DISK/TEMP/UPTIME strip, Services (with live detail/meta where available), Alerts, Storage & Backup
- Landscape Settings preview for OG (800×480) and V2 (1040×780), scaled to the Settings column
- Home **Edit order**: drag tiles to place (grip handle); order autosaves

### Changed
- Household focus remains a separate fixed design; Custom stays freeform
- **Copy markup** uses Clipboard API with `execCommand` fallback for `http://` LAN contexts
- Fixed-design preview uses a full-width designer column (no clipped palette track)

### Fixed
- Status wall / Household preview no longer renders as a tall narrow strip on desktop
- Home Done / edit bar no longer stuck visible after leaving edit mode
- Cockpit overview/settings link uses `https://` (Cockpit TLS on :9090)

## 0.0.1 — initial

First dashboard cut on the platform path install.
