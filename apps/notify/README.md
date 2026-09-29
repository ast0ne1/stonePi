# Notify

**Part of [StonePi](../../README.md)** — central Displays and Outputs (platform service).

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/notify/ |
| Local (port) | http://127.0.0.1:8012/ |

Shared StonePi sign-in. **Not a launcher tile** (`launcher: False`) — like Auth, it appears on **Services** as an always-on platform card.

## What it does

- **Displays** — one per TRMNL screen (built-in Dashboard Display + as many as you add). Each has a drag-and-drop **screen builder** over a real render of the device, and its own TRMNL webhook, interval and device (TRMNL OG 800×480 or TRMNL X 1040×780).
- **Settings → Destinations** — **ntfy** phone alerts (TRMNL moved onto each Display).
- **Event prefs / history** — which events may notify; delivery log.
- **Ingest** — `POST /api/events` (loopback) via `stonepi_contracts.emit_event()`.

Apps own data; Notify owns delivery. External failures never break apps.

## Platform notes

| Field | Value |
|-------|--------|
| Path | `/notify/` |
| Port | `8012` |
| Unit | `stonepi-notify` |
| User | `stonepi-notify` |
| Data | `/var/lib/stonepi/notify` |
| Packages | `stonepi_contracts`, `stonepi_display`, `stonepi_notify` |

Vault keys: `STONEPI_NTFY_TOKEN`; TRMNL webhooks `DISPLAY_WEBHOOK_URL` (built-in Dashboard Display) and `DISPLAY_WEBHOOK_URL_<ID>` (other Displays).

Dashboard Settings → Display redirects here. The push scheduler runs in this process and pushes each enabled Display on its own interval; Dashboard automations call `POST /api/push-trmnl` (pushes every Display with TRMNL on).

## TRMNL Displays

1. On trmnl.com, create one **Private Plugin** per screen and copy its webhook URL.
2. In Notify → Displays, open (or add) the Display, paste the webhook under **TRMNL**, and switch **Push to TRMNL** on.
3. Paste the **universal template** (Display → TRMNL → *Universal template for the plugin*) into the plugin's **Full** markup once. The layout travels in each push (`L` = `[[code, x, y, w, h], …]`, `G` = `[cols, rows]`), so rearranging widgets never needs a re-paste.
4. Lay out widgets in the builder. The preview renders the same template with the same payload a push sends, styled by the TRMNL Framework (3.2.0, loaded from trmnl.com), with sample or live data.

Pushes send only the variables the Display's widgets read, trimmed to TRMNL's 2 KB standard-plan limit. TRMNL allows 12 pushes an hour per plugin on the standard plan, so intervals are 10–120 minutes.

Upgrading: the old single TRMNL destination moves onto the Display it pushed, in **legacy** mode (its plugin still holds the older pasted markup, so every flat variable is still sent). Paste the universal template and switch the Display to it to use the builder layout.
