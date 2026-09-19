# Changelog — Dashboard

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

- Home launcher, Overview, Users, Services, Settings (Display, Watch, Vault, Automations, Updates, Backup, About)
