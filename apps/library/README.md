# Library

Wikipedia and other references, offline on your Pi. StonePi handles setup, storage, downloads and backups. Reading, search and browsing come from [Kiwix](https://kiwix.org) (`kiwix-serve`), unchanged.

| | |
|---|---|
| Path | `/library/` (manager), `/library/read/` (Kiwix, behind StonePi sign-in) |
| Ports | 8009 (Library), 8014 (Kiwix, loopback only) |
| Units | `stonepi-library`, `stonepi-kiwix` (enabled by the helper once content exists) |
| Data | `/var/lib/stonepi/library` (`library.sqlite`, `library.xml`, `catalog.json`, `backup-manifest.json`, default `zim/`) |
| Root helper | `/usr/local/sbin/stonepi-library-helper` (apt, mounts, Kiwix unit, backup setting) |

## How it fits together

- `app/services/catalog.py` reads the Kiwix OPDS catalogue (cached daily) and its `.meta4` files (exact size + SHA-256).
- `app/services/downloads.py` is a worker thread with jobs in SQLite. It resumes with Range requests, verifies, then installs.
- `app/services/kiwix.py` regenerates `library.xml` from the database and runs Kiwix only while there's content.
- `app/services/backup.py` handles the opt-in content backup and the capacity check. `deploy/backup/stonepi-backup-library.py` does the copy.
- `GET /_auth` is nginx's `auth_request` for the reader.

## Dev

`run-dev` starts it on port 8009. On Windows the root helper is simulated, so "Set up" succeeds without Kiwix. Real catalogue downloads work, and the Wikipedia sample (about 300 MB) is a quick end-to-end check. The reader itself only runs on the Pi.

```bash
cd apps/library && .venv/Scripts/python -m pytest tests -q
```
