## Build type: Interactive Guide

Make a short guided experience: how-to steps, choose-your-path story, or lesson.

### Goals
- Clear progress (Step 1 of N, Next / Back, or chapter buttons).
- Short paragraphs kids can read; cheerful illustrations with CSS/emoji/SVG ok.
- Finish with a “You’re done!” moment or summary.

### Phone first (iPhone 13 mini class)
- One step per screen on narrow phones; Next/Back as large bottom buttons.
- Progress dots or “Step X of Y” stay readable at 375px.
- Body copy wraps cleanly; no horizontal swipe carousels that fight scrolling.
- Pad bottom actions with `safe-area-inset-bottom` so thumbs can reach them.
- Desktop can show a slightly wider reading column (max ~40rem), still mobile-first CSS.

### Structure
- Prefer `index.html` + `style.css` + `app.js` that swap panels/sections (no full page reloads).
- Keep content in the HTML or a small JS data array so it is easy to edit later.
- Include a visible progress cue (dots, bar, or “Step X of Y”).

### Avoid
- Walls of text, autoplay sound, or infinite scrolling feeds.
- External video embeds unless truly needed (and only `https://`).
- Tiny “chip” controls clustered in a corner.
