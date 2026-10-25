# Ripple

Keep family far away up to date with news and photos. People granted Ripple post short
updates. The newest ones appear on a TRMNL e-ink screen at the family's home, wherever it is,
which Notify updates by pushing a picture of Ripple's display page. Nothing comes back from
the screen.

Spec and decisions: [`ideation/ripple.md`](../../ideation/ripple.md) (F16).

| | |
|---|---|
| App id | `ripple` |
| Path | `/ripple/` |
| Port | 8017 |
| Unit | `stonepi-ripple` |
| Data | `STONEPI_DATA_DIR` (`/var/lib/stonepi/ripple` on the Pi, `apps/ripple/data` in dev): `ripple.sqlite` and `media/` (photos and thumbnails) |

## Who can do what

- **Anyone granted Ripple** in Dashboard → People can read the feed and post. Access is checked
  on every request, so removing the grant takes effect immediately.
- **Authors** can edit and delete their own posts. **Admins** can edit or delete any post and
  rename the board.
- **Standalone** (no session secret): everything is allowed and posts are by "You".

## Status

- **Built (Phase 2):** feed, posting, editing, deleting, the "Show on the family display" switch,
  board name, run-dev.
- **Pi wiring:**
  - nginx `/ripple/` (HTTP and TLS) with static files served by nginx.
  - `stonepi-ripple.service` (user `stonepi-ripple`) in `stonepi.target`.
  - `install.sh` sets up the user, `/etc/stonepi/ripple.env`, the venv, the data directory and
    vault access.
  - Backup, restore and the `stonepi` CLI include it.
  - Not yet run on a Pi.
- **Built (Phase 3):**
  - Photos with captions, in `media/` beside the database. They're checked by decoding,
    re-saved without EXIF/GPS, given a thumbnail, and served only at `/media/<key>` to people
    with Ripple.
  - Admins set the limit per post in Settings → Photos.
- **Built (Phase 4):**
  - **The page.** `/display` is the e-ink page, in a hero or list layout, with photos dithered
    here for the panel.
  - **Access.** It's open to members as a preview, and to Notify's capture with a one-off key
    from `POST /api/internal/capture-key` (signed). The key works once, for 120 s, only from
    loopback and never through nginx.
  - **The nudge.** After changes Ripple sends `POST {notify}/api/internal/web/changed`.
  - **Our time and weather** (optional, Settings → Family display): a header line such as
    "Copenhagen · Sunday evening · 10 h behind you · 8° overcast". It shows the part of the day
    and the time difference, not a clock, because the screen only updates now and then. Weather
    comes from Open-Meteo, and both cities are picked with its place search (`app/local.py`).
    Ripple needs outbound HTTPS to `api.open-meteo.com` and `geocoding-api.open-meteo.com`.
  - **Setup.** Settings → Family display → **Set up the family display** opens Notify's
    Add a Display with "Ripple family board" chosen. It asks only for a name, the model and
    the webhook; Notify sets up the rest and sends the first image. The same page shows how
    each family Display is doing.

On a Pi that's already installed, the simplest way to add Ripple is to run `deploy/install.sh`
again. It creates the `stonepi-ripple` user, env file, venv and data directory. An overlay that
only copies files won't do this.

## Dev

`python scripts/run_dev.py` starts it on http://127.0.0.1:8017/. Tests (from `apps/ripple`):

```
.venv\Scripts\python.exe -m pytest tests
```
