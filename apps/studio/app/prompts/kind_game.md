## Build type: Game

Ship a **real, playable browser game** that someone can enjoy for several minutes. It is played mostly on a **laptop or desktop with a keyboard**, and must also work on a phone. Give it clear rules, satisfying feedback, and a reason to try again. It is not a mock UI, a static picture with a score label, or "tap the emoji to add points".

### Quality bar: the build fails if any of these are true
- There is no continuous game loop (`requestAnimationFrame` with delta time).
- There is no collision / hit-test / rule engine for the genre.
- The graphics are only emoji, text, or a flat box with no drawn motion.
- The only input is "click/tap anywhere on the playfield".
- Keyboard play is missing, or there are no on-screen touch controls for phones.
- Title, HUD, game over, or a working Restart is missing.
- It is clipped or unusable at 1280×800 (main target) or at 390×844, or it scrolls horizontally.

### Required systems (in `app.js`)
1. **State machine:** `title` → `playing` ⇄ `paused` → `gameover` (or `win`), driven by player actions and real win/lose conditions.
2. **Loop:** `requestAnimationFrame`, with separate `update(dt)` and `draw(ctx)`. Cap `dt` (e.g. 0.05s) so a background tab doesn't cause teleporting.
3. **Rules & scoring:** score plus lives / timer / level, and difficulty that ramps over time. Keep a **best score** in `localStorage` and show it on the title and game-over screens.
4. **Entities:** player, hazards and pickups as data objects (position, size, velocity or grid cell), not hand-moved DOM.
5. **Feedback:** visible reactions to hits, pickups and misses (flash, particles, shake). Web Audio beeps are optional; start them only after a user gesture, and provide a mute toggle.

### Graphics
- A single `<canvas>` playfield with a `devicePixelRatio` backing store for sharpness. On `resize`, rescale the drawing while keeping the logical game coordinates fixed.
- Draw distinct shapes/paths or procedural sprites, and give the background some treatment (gradient, parallax, pattern).
- Show a HUD with score, lives/level and best score. The title screen shows the controls: keys on desktop, buttons on touch.
- No WebGL engines, CDN frameworks or big asset packs. Procedural canvas art is preferred.

### Controls (both schemes)
- **Keyboard (primary):** arrows **and** WASD. Space/Enter for the main action or start. P/Esc to pause and resume. `preventDefault()` only while playing or paused.
- **Touch (phone compatible):** an on-screen control cluster *below* the playfield, with buttons ≥ 44×44px. Use `pointerdown`/`pointerup`/`pointercancel`, with hold-to-move for continuous actions and `touch-action: none` on the pad. It must mirror the keyboard 1:1. Show it on touch devices (`@media (pointer: coarse)`); on desktop, hide it or keep it compact so the playfield gets the space.
- **Start/Restart/Pause** are real buttons (≥ 44px) and are also keyboard-reachable. Pressing Start focuses the game, so keys work inside the Studio preview iframe.
- Portrait is the phone default. If phone landscape is awkward, show a short "rotate to portrait" hint.

### Layout
```
┌──────────────────────┐
│ title · score · best │
│      PLAYFIELD       │  canvas, as large as fits
├──────────────────────┤
│  on-screen controls  │  thumb reach, safe-area padding
└──────────────────────┘
```
- Laptop/desktop: a centred shell of about 640–900px, with the playfield as large as fits the viewport height (no scrolling to see the bottom). Phone (≤480px): full-width shell with controls below. The HUD, playfield and controls must fit in one viewport.
- Make it feel like a proper arcade game: juicy feedback, a satisfying game-over screen showing the score and best score, and a "Press Space to play again" prompt.

### Genre
Match what the user asked for. If they're vague, pick **one** genre and do it fully: endless runner, breakout, snake, flappy-style flyer, frogger-style crosser, top-down collector, simple platformer, memory match with a timer, or falling blocks (with rotate/soft-drop buttons). Don't build a menu of mini-games unless asked.

### Clarify vs build
- **Clarify:** ask about genre, theme, how you win or lose, and difficulty. Propose defaults for each so the user can just press Build.
- **Build:** deliver a complete first playable that already clears the quality bar. Rebuilds refine it.

### Keep it family-friendly
- Cartoon arcade action (blasting asteroids, bopping robots) is fine. Avoid gore, realistic weapons, gambling or loot boxes, paywalls, accounts, and online multiplayer.
- No placeholder physics, fake buttons, or score that rises without skill.
