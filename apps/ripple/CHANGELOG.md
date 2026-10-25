# Changelog — Ripple

## 0.0.10 — 2026-10-25

### Added
- **New app: Ripple** (`apps/ripple`, `stonepi-ripple`, port 8017): family updates for a TRMNL
  e-ink display (`ideation/ripple.md`, F16).
  - **Feed:** newest first, with author, date and an "edited" note, 20 posts a page. Photos show
    as small previews: one up to 260 px, several as square tiles, three to a row on a phone.
  - **Posts:** new, edit and delete, each with a **Show on the family display** switch (on by
    default).
  - **Who can do what:** anyone granted Ripple can post. Authors change their own posts; admins
    can change any post. Access is checked on every request.
  - **Settings:** board name (admin) and About.
  - Stored in SQLite (`ripple.sqlite`) with versioned migrations.
  - **Photos:** each post can have photos with captions (up to 4 by default; admins can
    change it in Settings → Photos).
    - The post form shows previews, a caption box per photo and an upload progress bar. If
      something's wrong it says so without losing what you wrote.
    - Every photo is checked by actually opening it (JPEG, PNG or WebP, up to 20 MB), turned
      upright, and saved again **without its location or camera details**.
    - Photos are only shown to people with Ripple and are never public. Removing a photo or
      deleting a post deletes the files straight away.
    - Big photos are kept at up to 4096 px on the long side; very large JPEGs are read at a
      reduced size. Photos are prepared one at a time, so uploads stay light on the Pi.
  - **Family display** (`/display`): the page Notify captures for the family's TRMNL.
    - It shows the newest posts marked for the display, with the photos large or in rows
      (Settings → Family display), and photos are prepared for e-ink on the Pi.
    - **Several photos together:** the newest photo, plus any others from the last 3 days, up to
      4: one fills the photo area, two stack, three are one above two, four are two by two, with
      white gaps between them.
    - **Dates the family can read:** each post shows the day and date with a 12-hour time
      ("Sun 25/10 8:31 AM") instead of "Today", which could be wrong in the family's time zone.
    - Notify opens it with a one-off key that works once, for two minutes, from the Pi itself.
      Anyone with Ripple can preview it.
    - After any change Ripple tells Notify, which updates the screen within a minute or so.
    - **Our time and weather** (optional, Settings → Family display): a line at the top of the
      family's screen, e.g. "Copenhagen · Sunday evening · 10 h behind you · 8° overcast".
      - It shows the part of the day and the time difference rather than a clock (the screen
        updates now and then), following daylight saving on both sides.
      - The weather comes from Open-Meteo, refreshed every half hour.
      - Both cities are picked with a place search.
    - **Setting it up is one button:** Settings → Family display → Set up the family display.
      You pick the TRMNL model and paste the webhook; the page then shows when each family
      screen last updated.
  - **Pi:**
    - nginx `/ripple/` (HTTP and TLS) and the `stonepi-ripple` unit (user `stonepi-ripple`, port
      8017).
    - `install.sh` sets up its user, env file, venv and data directory.
    - Backups take a consistent copy of `ripple.sqlite`, and restore fixes its ownership.
    - The `stonepi` CLI includes it.
