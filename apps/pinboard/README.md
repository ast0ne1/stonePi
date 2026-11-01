# Pinboard

**Part of [StonePi](../../README.md)** — the household's board: notes, links, reminders and checklists.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/pinboard/ |
| Windows `run-dev.bat` | http://127.0.0.1:8004/ |

Shared StonePi sign-in. Access requires a **Pinboard** app grant (admins see it by default). Members also need **Add and pin cards** to add cards and pin them to the displays, and **Assign reminders to others** to assign a reminder to someone else or the whole household; without it their reminders are their own. Only admins remove items or set the reminder time.

## Why it’s in StonePi

A shared board for the household, in a list or laid out freely — not a full task manager. Pins and reminders show on phones and, on a **TRMNL** display or the Car Thing panel (set up in Notify → Displays).

## What it does

- **Board** — notes, links (with a fetched preview), reminders and checklists, grouped by area on a cork, plain, dotted or whiteboard background. Pin a card (four colours, four styles) to put it on the displays; tick reminders and checklist items done
- **Canvas** — the same cards laid out freely on one big board (the default on wide screens; **List / Canvas** at the top): drag the background or two fingers to move around, pinch or ctrl/cmd + scroll to zoom, drag a card to move it and its corner to resize it, drag an area by its name with its cards, and double-click empty space to add a card there. On a laptop it fills the window. Other people's moves show within a few seconds
- **Notices** — short household messages everyone with access can see
- **Reminders** — due date and optional assignee
- **Settings → Board** — your background, the board's areas, removing done items after a set number of days, and adding links from anywhere (bookmarklet, iPhone Shortcut, Android share sheet)
- Phone-first UI in the shared StonePi design system (light, dark or system theme)

## Integrations

| Integration | Role |
|-------------|------|
| **TRMNL / Display** | `GET …/api/display` supplies the Pinboard block for Notify → webhook push. Removing a pin can clear it from the next push. Public nginx denies this path at the edge when internet-facing; collection still runs via loopback. |
| **PriceScout** | `POST /api/reminder` (JSON, session cookie + CSRF) creates a reminder from a shopping list (the caller's own reminder when they can't assign to others). |

## Platform notes

- Not a Settings “platform tab” — it is a **launcher app**.
- Users / backup / updates are managed on the Dashboard when running under StonePi.
- Keep copy short; Display is the glance surface, Pinboard is the edit surface.

## Solo / standalone

Pinboard expects platform auth. Use `scripts\run-dev.bat` from the StonePi root so Auth and the session cookie are available.
