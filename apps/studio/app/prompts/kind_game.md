## Build type: Game

Ship a **real, playable browser game** — not a mock UI, not a static illustration with a score label, and not “tap emoji to increment points.” Visitors on **iPhone 13 and newer** (and similar Androids) **and** on **laptop / desktop screens** must be able to **play for several minutes** with clear rules, feedback, and a reason to try again. Publish target is **FileServe** on the household LAN.

### Target viewports (required)

Design and test mentally against **all** of these — not only a tiny phone demo:

| Class | CSS viewport (approx.) | Notes |
|-------|------------------------|--------|
| **Phone baseline** | **390×844** (iPhone 13 / 14) | Primary phone target. Also OK at 375×812 (13 mini) and 393×852 (14 Pro). |
| **Phone+** | **430×932** (iPhone 14/15 Pro Max class) | Larger phones — scale playfield up; controls stay thumb-reachable. |
| **Laptop** | **1280×800** and up | Centered play shell; keyboard is primary; on-screen pads may stay visible or compact. |
| **Desktop** | **1440×900** / **1920×1080** | Same shell, larger max width; never stretch the board edge-to-edge across ultrawide. |

**Rules for every size:**

- No horizontal scroll; no clipped Start / score / critical controls.
- Use `100dvh` (with `min-height` fallback) and `env(safe-area-inset-*)` on phones (notch + home indicator).
- Canvas / playfield uses **devicePixelRatio** backing store so graphics stay sharp on Retina phones and HiDPI laptops.
- Resize: on `resize` / `orientationchange`, recompute playfield size from the shell; keep **logical game coordinates** stable (scale draw, don’t break collision math).
- Portrait is the phone default. If landscape on a phone is awkward, show a short “rotate to portrait” hint rather than a broken layout.
- Laptop/desktop: playfield + HUD fit in the first viewport without trapping the player behind browser chrome; mouse click on UI buttons is fine, but **keyboard remains fully sufficient to play**.

### Quality bar (reject shallow builds)

A build **fails** if any of these are true:

- No continuous game loop (`requestAnimationFrame` with delta time, or equivalent).
- No collision / overlap / hit-test logic (or equivalent rule engine for the genre).
- Graphics are only emoji, plain text, or a blank colored box with no drawn motion.
- The only input is “click / tap anywhere on the playfield.”
- Keyboard works but there are no on-screen controls (or the reverse on phones).
- Title / HUD / game-over are missing, or Restart does nothing.
- Unusable or clipped on **iPhone 13-class (~390×844)** or on a **laptop (~1280×800)** viewport.
- Horizontal scroll, or controls that sit under the iOS home indicator without safe-area padding.

Aim for something closer to a polished mini-arcade / puzzle / endless / platformer prototype than a classroom demo.

### Required systems

Implement all of the following in `app.js` (or clearly split modules still loaded from `index.html`):

1. **State machine** — at least: `title` → `playing` → `paused` (optional but preferred) → `gameover` / `win`. Transitions must be driven by player action or real failure/success conditions.
2. **Game loop** — `requestAnimationFrame`; update physics/AI with **delta time**; separate `update(dt)` and `draw(ctx)` (or DOM render) clearly.
3. **Rules & scoring** — score (or lives / timer / level); win **or** lose condition that the player can understand; increasing challenge over time (speed, spawn rate, density, fewer cues — pick what fits).
4. **Entities** — player plus hazards / targets / tiles / pieces as data objects with position, size, and velocity or grid coords — not one-off hardcoded DOM moves.
5. **Feedback** — visible reactions on hit / collect / miss (flash, particles, shake, sound-optional via Web Audio beeps). Score and state changes must be obvious without reading the chat.

### Graphics (required)

Prefer a single **`<canvas>`** playfield (2D context). CSS/DOM games are allowed only if motion and collisions are still real.

**Must include:**

- Drawn shapes, paths, or simple sprite sheets (CSS gradients / canvas fills / stroked silhouettes). Characters should read as distinct objects, not lone emoji.
- A readable background treatment (gradient, parallax strips, tiled pattern, or scene layers) — not a flat white page.
- Smooth motion every frame while playing; idle title screen may animate lightly.
- HUD drawn or overlaid: score, lives/level, and short control hint on the title screen (keyboard hints on desktop, touch hints on phone).

**Keep light:** no WebGL engines, no multi‑MB asset packs, no external CDN game frameworks. Inline tiny base64 sprites only if needed; prefer procedural canvas art.

### Input model (required — both schemes)

Never use “click / tap the playfield” as the primary control (no click-to-drop Tetris, no tap-canvas-to-move).

**1. Desktop / laptop (keyboard)**

- Movement: Arrow keys **and** WASD (both).
- Primary action: Space and/or Enter (jump, rotate, shoot, confirm, dash — genre-appropriate).
- Pause: `P` or `Escape` when playing; same key resumes.
- Track keys with a `keydown` / `keyup` map; `preventDefault()` **only while playing/paused** so the page does not scroll.
- No hover-only or right-click requirements. Focus the game shell on load / Start so keys work immediately.
- Entire run must be completable with keyboard alone on a laptop trackpad machine (no reliance on precise mouse aim unless the genre is explicitly mouse-aim **and** you still provide a keyboard/touch alternative).

**2. Phone / touch (iPhone 13+)**

- Dedicated **on-screen control cluster** outside the playfield (bottom cluster preferred).
- Every control ≥ **44×44px**; use `pointerdown` / `pointerup` / `pointercancel` (hold-to-move for D-pad / continuous actions).
- `touch-action: manipulation` on controls; block scroll while dragging the D-pad.
- Mirror keyboard actions 1:1 (same left/right/up/down/action/pause semantics).
- Safe-area padding (`env(safe-area-inset-*)`) so controls clear the home indicator and notch.
- One-thumb reachable: primary action near the bottom; do not put the only action button in a top corner.

**Layout shell**

```
┌─────────────────────┐
│  title / score HUD  │
│                     │
│      PLAYFIELD      │  ← canvas or dedicated board, not full-page clicks
│                     │
├─────────────────────┤
│  on-screen controls │  ← required on phone; OK to keep on desktop
└─────────────────────┘
```

**Responsive shell sizing**

- **Phone (≤480px wide):** full-width shell; playfield `width: 100%`; controls full width under the board; `padding` uses safe-area insets.
- **Phablet / small tablet (481–900px):** centered shell `max-width: 480–520px`.
- **Laptop / desktop (≥901px):** centered shell `max-width: 560–640px` (or playfield + side control column if that fits without shrinking the board below ~360px wide). Leave breathing room in the viewport; do not force a 200px-tall canvas on a 1080p screen.
- Start / Restart / Pause are large buttons (≥44px) **and** keyboard-reachable.

### File structure

Prefer:

- `index.html` — shell, canvas, HUD, control buttons with clear `aria-label`s
- `style.css` — full-viewport layout, breakpoints above, safe areas, no horizontal scroll
- `app.js` — loop, state, input, entities, draw, resize/DPR handling

Include viewport meta with `viewport-fit=cover`. Relative asset paths only.

### Genre guidance (pick what the user asked for)

If the user is vague, choose **one** coherent genre and execute it fully (examples): endless runner, breakout/arkanoid, snake, match-3 light, frogger-style crosser, flappy-style flyer with pipes, top-down collector, simple platformer (gravity + solid tiles), memory/pair match with timer, or falling-block puzzle **with button/keyboard rotate & soft-drop (not click-to-place)**.

Match art and controls to that genre. Do not ship a “menu of mini-games” unless asked.

### Clarify vs build

- **clarify** — Ask what genre, theme, win condition, and difficulty they want. Do not emit a file map.
- **build** — Deliver a complete first playable in one go. Preview must be fun to try immediately on phone **and** laptop; polish in later rebuilds is fine, but the first build must already clear the quality bar above.

### Avoid

- Violence, gambling themes, paywalls, accounts, or online multiplayer.
- Heavy engines (Phaser, Three.js, Unity WebGL), bundlers, or npm.
- Mouse-only or hover-only schemes; playfield-tap as the sole input.
- Phone-only layouts that leave a postage-stamp game on desktop, or desktop-only fixed widths that overflow on iPhone 13.
- Placeholder “TODO” physics, fake buttons that do not change game state, or score that increments with no skill.
