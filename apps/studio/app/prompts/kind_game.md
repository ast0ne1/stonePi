## Build type: Game

Make a simple browser game that is fun in under a minute to learn, and that stays playable after publish to **FileServe** (phone + desktop browsers on the household LAN).

### Goals
- One screen flow: **title → play → score / win / try again**.
- Friendly art with CSS shapes, emoji, or simple canvas — keep files small.
- Gentle difficulty by default; optional “hard” toggle is fine.
- Prefer `index.html` + `style.css` + `app.js` (canvas or DOM).

### Input model (required — do not skip)

Games must be controllable without relying on “click / tap the playfield” as the primary control. Click-to-place or click-to-drop on the board feels broken on phones and in FileServe iframes.

**Always provide both:**

1. **Desktop / keyboard**
   - Arrow keys and/or WASD for movement; Space / Enter for primary action (rotate, jump, shoot, confirm).
   - `keydown` / `keyup` with `event.preventDefault()` only while the game is focused / playing (so the page does not scroll).
   - Do not require hover or right-click.

2. **Phone / touch**
   - A dedicated **on-screen control cluster** outside the playfield (bottom or side), each control ≥ **44×44px**.
   - Use `pointerdown` / `pointerup` / `pointercancel` (or touch equivalents) on those buttons — not `click` alone.
   - `touch-action: manipulation` on controls; prevent page scroll while dragging on the D-pad when sensible.
   - Safe-area padding (`env(safe-area-inset-*)`) so controls clear the home indicator.

**Layout**
- Keep the **playfield visually separate** from controls. Never overlay tiny invisible hit targets on the board as the only input.
- Portrait first (~375×812). On wider screens, keep the playfield centered and controls reachable without stretching the board past the viewport.
- Fit score + restart in the visible viewport; restart must be one tap / one key.

### Phone + FileServe hosting
- Include viewport meta with `viewport-fit=cover`.
- No horizontal scroll; playfield scales to fit width.
- Relative asset paths only; works inside FileServe hosted pages and Studio’s preview iframe.
- Pause or soft-fail if the screen is too short rather than clipping critical UI.

### Avoid
- Violence, gambling themes, paywalls, or online multiplayer.
- Heavy asset packs, WebGL engines, or downloads outside the zip.
- Mouse-only or hover-only controls with no keyboard **and** no on-screen buttons.
- “Tap anywhere on the canvas to move/rotate” as the sole control scheme (classic Tetris-style click-to-drop is not acceptable).
