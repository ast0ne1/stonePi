# Pinboard

**Part of [StonePi](../../README.md)** — household notices and short reminders.

| Mode | URL |
|------|-----|
| On the Pi | http://stonepi.local/pinboard/ |
| Windows `run-dev.bat` | http://127.0.0.1:8004/ |

Shared StonePi sign-in. Access requires a **Pinboard** app grant (admins see it by default).

## Why it’s in StonePi

A lightweight shared corkboard — not a full task manager. Pins and reminders show on phones and, when Destinations are configured, on a **TRMNL** e-ink panel.

## What it does

- **Board** — all notices and reminders as expandable cards (type pill + icon; due date when present)
- **Notices** — short household messages everyone with access can see
- **Reminders** — due date and optional assignee
- Phone-first UI aligned with NewsCast-style chrome (themes / palettes)

## Integrations

| Integration | Role |
|-------------|------|
| **TRMNL / Display** | `GET …/api/display` supplies the Pinboard block for Notify → webhook push. Removing a pin can clear it from the next push. Public nginx denies this path at the edge when internet-facing; collection still runs via loopback. |
| **PriceScout** | `POST /api/reminder` (JSON, session cookie + CSRF) creates a reminder from a shopping list. |

## Platform notes

- Not a Settings “platform tab” — it is a **launcher app**.
- Users / backup / updates are managed on the Dashboard when running under StonePi.
- Keep copy short; Display is the glance surface, Pinboard is the edit surface.

## Solo / standalone

Pinboard expects platform auth. Use `scripts\run-dev.bat` from the StonePi root so Auth and the session cookie are available.
