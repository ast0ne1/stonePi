## Build type: Handy App (single page)

One screen that does one job well: a checklist, converter, quiz, timer, scoreboard, planner or similar.

### Goals
- A clear purpose the visitor understands in five seconds: a short title, one helper sentence, then the tool.
- The primary action is obvious and reachable without scrolling on a 390px phone.
- State survives a refresh (`localStorage`). Offer a clear "Reset" when there is data to lose, and confirm it with in-page UI.
- Friendly, high-contrast colours. Include empty states ("No items yet — add one above") rather than blank areas.

### Layout
- Laptop first: a centred column (about 40–48rem), or two columns when it genuinely helps (e.g. form + results).
- Keyboard: Enter submits forms, Tab order follows the screen, focus is visible, and add shortcuts where natural (e.g. N for a new item).
- Phone compatible: one column, full-width buttons, list rows at least 44px tall. Sticky bottom bars respect `safe-area-inset-bottom`.
- Visible labels on every input. Icon-only buttons need an `aria-label` and should be rare.
- Make it delightful as well as useful: a little celebration when a list is completed or a goal is reached.

### Files
`index.html` (main UI in `<main>`), `style.css`, `app.js`. No frameworks, bundlers or npm.

### Clarify vs build
- **Clarify:** ask what the tool must do, what it should remember, and any must-have fields. Offer defaults.
- **Build:** ship a working tool with realistic sample content or a helpful empty state, not lorem ipsum.

### Avoid
- Multi-page navigation, logins, ads, tracking, or asking for personal data.
- Side-by-side panels that squash text on a phone.
