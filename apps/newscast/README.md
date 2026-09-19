# NewsCast

**Part of [StonePi](../../README.md)** — daily briefings from the feeds you choose, ready for an e-reader.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/news/ |
| Windows `run-dev.bat` | http://127.0.0.1:8001/ |

Shared StonePi sign-in (one cookie). On the Pi, **Users**, **Updates**, and **Backup** live under Dashboard Settings; NewsCast Settings keep Publication, Schedule, Feeds, Reader, LLM, Translation, and Notifications.

## Why it’s in StonePi

Household “what’s worth reading today” without five news apps — fetch, dedupe, brief, and deliver to **CrossPoint** (Xteink) or **KOReader** (Kobo) over OPDS. Also feeds a **Display / TRMNL** block when enabled.

## Platform notes

- **Grants / capabilities:** `can_add_custom_sources`, `can_use_ntfy`, `can_view_status` (Dashboard → Users).
- **Exposure:** home network vs internet-facing changes OPDS / X3 / sync token behaviour — see [deploy/SECURITY.md](../../deploy/SECURITY.md).
- **Vault keys:** `OPENAI_API_KEY`, `X3_SYNC_TOKEN`, `NEWSCAST_NTFY_TOKEN`, `NEWSCAST_READER_SSH_PASSWORD`, and related secrets preferred from Settings → Vault.
- **Display:** `GET …/api/display` for dashboard → TRMNL (denied at nginx edge when public).
- Under StonePi, apps bind loopback; nginx serves `/news/`. Per-app LAN HTTPS is for **solo** runs only.

<p align="center">
  <img src="docs/screenshots/login.png?v=0.0.0.8" alt="NewsCast sign-in screen on a phone" width="280" />
</p>

## What it does

- Fetches RSS on a schedule, or scrapes when no usable RSS is found
- Deduplicates the same story across outlets
- Writes a concise briefing (**OpenAI**, **Ollama**, or extracted text)
- Optional **translation** (Google or LLM) per feed or globally
- Scores importance; category slotting; per-category OPDS papers
- SQLite store; unfavourited stories drop after retention days
- Mobile-first UI (themes + palettes shared with other StonePi apps)
- Per-user feeds, papers, Send library, OPDS catalogs, ntfy topics
- OPDS for CrossPoint / KOReader; JSON / TXT / EPUB downloads
- Send queue for EPUB/PDF as-is; optional ntfy on publish / push

## Web UI

| Tab | What it is for |
| --- | --- |
| **Briefing** | Today / Yesterday, filters, star, bookmark as long-read |
| **Saved** | Paste a URL; scrape full text into the next briefing |
| **Search** | Stories, favourites, Saved long-reads |
| **Send** | Upload EPUB/PDF; push or queue for the reader |
| **Feeds** | Enable/mute, schedule, keywords, summarise vs full, translate |
| **Catalog** | Curated sources; admin approvals for household members |
| **Status** | Delivery health, OPDS URL, queue, QR (capability-gated) |
| **Settings** | General, Publication, Schedule, Filters, Translation, LLM, Reader, Notifications, Categories, Catalog — Users/Backup/Update when solo |

## Integrations (this app)

| Integration | Role |
|-------------|------|
| **E-readers** | OPDS catalogs; CrossPoint File Transfer or KOReader SSH/SFTP; optional X3 Sync API |
| **AI / LLM** | Briefing summaries |
| **Translation** | Feed text into a target language |
| **ntfy** | Phone alerts (capability-gated) |
| **TRMNL** | Overview/status block via Display |

## Household accounts

On StonePi, people are created on **Dashboard → Users** with a NewsCast grant. Each person gets their own feeds, paper, Send files, and `/opds/u/<username>` catalog.

- Catalog approvals and capability flags still apply inside NewsCast Settings where exposed.
- On Status, use **your** catalog URL (`/opds/u/<username>`), not admin `/opds`, for that person’s reader.

## E-reader

The reader talks to **one NewsCast at a time**. Set an **Instance name** so the briefing title shows which copy you pulled.

On Settings → Reader, pick **Xteink** or **Kobo**. Both use OPDS for today’s frozen EPUB and Send-tab files. Under StonePi, catalog URLs are typically `http://stonepi.local/news/opds` or `…/opds/u/<username>` (path may include the `/news` prefix depending on reverse-proxy rewrite — copy the URL from Status).

### Xteink (CrossPoint)

Add the OPDS server in CrossPoint. Leave username/password blank unless catalog login is on (Basic can crash CrossPoint). Push uses HTTP File Transfer to the reader host (default `crosspoint.local`).

### Kobo (KOReader)

Add the OPDS catalog in KOReader. Push uses KOReader’s **SSH server** (default port 2222) and SFTP into `/mnt/onboard/News` (or your chosen folder).

### Xteink Sync (optional)

Patched firmware can poll Sync-style APIs (`/api/v1/device/tasks`, etc.). Prefer Tailscale or a strong catalog token when internet-facing — see SECURITY.md.

## Notifications (ntfy)

Settings → Notifications: server, topic, token; events for publish and/or push. Non-admins need **Allow ntfy**.

## Solo / standalone run (optional)

Without `STONEPI_SESSION_SECRET`, `run-local.bat` runs NewsCast alone (default port **8080** on Windows). Export-pi / solo install scripts remain for a NewsCast-only Pi. Prefer the StonePi installer for the full portal.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
.\run-local.bat
```

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```
