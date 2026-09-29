## Build type: Story or Guide

A short guided experience, such as step-by-step how-to instructions, a choose-your-path story, or a mini lesson with a quick check at the end.

### Goals
- Clear progress at all times: "Step 2 of 6" plus a bar or dots. Next / Back are always available.
- Short, plain-language steps (1–3 sentences each). Pitch the reading level at the audience the user describes: simpler for kids, direct for adults.
- Illustrate with CSS/SVG/emoji where it helps understanding. Use a callout for warnings or tips.
- End with a clear finish ("You're done!" or a summary / ending), plus **Start again**.
- Remember the current step in `localStorage` so a refresh resumes where the visitor left off.

### Layout
- Laptop first: a readable centred column (max ~40rem) with room for an illustration. Optionally list steps in a side outline if there are more than 6.
- Keyboard: ← / → (and Enter) move between steps, number keys pick choices, and focus moves to the new step's heading.
- Phone compatible: one step per screen, with large Next/Back buttons at the bottom (thumb reach, safe-area padding).
- Choose-your-path: make choices large buttons, and allow going back to the previous choice.

### Files
`index.html`, `style.css`, `app.js`. Keep the steps in a small JS data array (title, body, optional choices) so they are easy to edit later. Swap panels in place with no page reloads.

### Clarify vs build
- **Clarify:** ask who it's for, the steps or story outline, and how it should end. Offer a sensible draft outline.
- **Build:** write the real content, not placeholders.

### Avoid
- Walls of text, autoplay sound or video, swipe-only carousels, or tiny controls in a corner.
- External embeds unless the user asks (and then `https://` only).
