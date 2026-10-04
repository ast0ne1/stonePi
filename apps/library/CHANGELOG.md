# Changelog

## 0.0.7 — 2026-10-04 (new app)

### Fixed
- PDFs in the reader open instead of "This page has been blocked by Chrome" (nginx drops Kiwix's sandbox CSP for PDFs only); signing in from a reader link returns to it instead of a 404.
- **Custom folder** storage works on a Pi: the root helper creates and hands over the folder before it's checked, and `/media/<user>` drives are opened to the Library and its reader.
- Installing Kiwix waits for the apt lock and runs outside the Library's memory limit.
- A Kiwix search link that needed sign-in keeps its own encoded characters after signing in.

### Added
- **Browse → Something else** searches the whole Kiwix catalogue by keyword (the titles browse.library.kiwix.org shows: Stack Exchange, TED, DevDocs, Gutenberg and more), household language first, each result installable in place.
- Links copied from browse.library.kiwix.org are understood: viewer links (`/viewer#book`), article links (`/content/book/A/Page`) and the browse page's own filter links (`#books.name=…`).
- Set up the Kiwix reader from the Library page (microSD, a USB stick or SSD, or a custom folder). Nothing is downloaded until content is chosen.
- **Browse:** featured Wikipedia, Wikivoyage, Wiktionary, Project Gutenberg and a 300 MB Wikipedia sample from the Kiwix catalogue. Each size shows whether it fits. The language is set in Settings → Content. Any other Kiwix title can be installed by name or link.
- **Downloads:** in the background, resumable, verified against Kiwix's SHA-256, with a progress bar that updates live. Cancel, Retry (resumes) and an optional speed limit.
- **Collections:** Open in the reader, Update (safe swap, or replace in place when both copies don't fit), Remove (only the recorded file is deleted).
- **Settings → Storage:** move the library between microSD and USB. The library stays readable until the switch.
- **Settings → Backup:** include library files in USB backups after a capacity check (required, margin, what's left). A shared backup drive gets a clear "second copy on the same drive" warning. Re-checked after every content change.
- **Settings → Reader:** Kiwix status, Restart, Remove the Library.
- Notify events: content installed, download failed, update available, storage missing, backup capacity.
### Security
- Only admins can choose "Another folder" storage, enforced on the server (it was only hidden in the page while the root helper prepared any typed path).
- `/_auth` (the Kiwix reader's gate) and every page fail closed when the session secret is missing outside dev.
- A missing root helper on the Pi reports "Library helper not installed — re-run the installer" instead of simulating success.

### Changed
- Setup, changing storage, including library files in backups and the download speed limit are admin-only; "Manage content" keeps install, update, remove, downloads, catalogue refresh, language and reader restart. Browse no longer starts overlapping catalogue refreshes.

