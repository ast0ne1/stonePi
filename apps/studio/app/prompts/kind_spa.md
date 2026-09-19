## Build type: Single Page App

Make one self-contained tool or mini-app that lives on a single screen.

### Goals
- One clear job (counter, checklist, converter, quiz, sticky notes, etc.).
- Friendly colours — kid- and family-friendly.
- Works offline in the browser once loaded (no backend).

### Phone first (iPhone 13 mini class)
- Design the whole UI for ~375px width; enlarge gracefully on desktop.
- Keep the primary action visible without scrolling when possible.
- Sticky footer bars must respect `safe-area-inset-bottom`.
- Prefer full-width buttons stacked; avoid multi-column toolbars on small screens.
- If you show a list, each row should be easy to tap (min ~44px tall).

### Structure
- Prefer `index.html` + `style.css` + `app.js`.
- Put the main UI in a `<main>` landmark with a short title and helper sentence.
- Use buttons and inputs with visible labels; avoid tiny icon-only controls without text.

### Avoid
- Multi-page navigators, frameworks, bundlers, or npm.
- Dark patterns, ads, tracking, or asking for personal data.
- Side-by-side panels that crush text on a mini phone.
