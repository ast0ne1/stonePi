(() => {
  const N = 1;
  const E = 2;
  const S = 4;
  const W = 8;
  const SIZE = 7;
  const CELL = 72;
  const PAD = 48;
  const BOARD_PX = PAD * 2 + SIZE * CELL;

  const PLAYERS = [
    { id: 0, name: "Amber", color: "#d97706", start: [0, 0] },
    { id: 1, name: "Jade", color: "#059669", start: [0, 6] },
    { id: 2, name: "Sky", color: "#2563eb", start: [6, 0] },
    { id: 3, name: "Rose", color: "#e11d48", start: [6, 6] },
  ];

  const TREASURES = [
    { id: "owl", label: "Owl", glyph: "🦉" },
    { id: "map", label: "Map", glyph: "🗺️" },
    { id: "gem", label: "Gem", glyph: "💎" },
    { id: "key", label: "Key", glyph: "🗝️" },
    { id: "ring", label: "Ring", glyph: "💍" },
    { id: "book", label: "Book", glyph: "📖" },
    { id: "crown", label: "Crown", glyph: "👑" },
    { id: "lantern", label: "Lantern", glyph: "🏮" },
    { id: "scroll", label: "Scroll", glyph: "📜" },
    { id: "compass", label: "Compass", glyph: "🧭" },
    { id: "chalice", label: "Chalice", glyph: "🏆" },
    { id: "amulet", label: "Amulet", glyph: "🔮" },
    { id: "sword", label: "Sword", glyph: "🗡️" },
    { id: "shield", label: "Shield", glyph: "🛡️" },
    { id: "coin", label: "Coin", glyph: "🪙" },
    { id: "flute", label: "Flute", glyph: "🎶" },
    { id: "boot", label: "Boot", glyph: "🥾" },
    { id: "ghost", label: "Ghost", glyph: "👻" },
    { id: "dragon", label: "Dragon", glyph: "🐉" },
    { id: "spider", label: "Spider", glyph: "🕷️" },
    { id: "bat", label: "Bat", glyph: "🦇" },
    { id: "rat", label: "Rat", glyph: "🐀" },
    { id: "moth", label: "Moth", glyph: "🦋" },
    { id: "scarab", label: "Scarab", glyph: "🪲" },
  ];

  // Fixed tile openings for classic-style 7×7 board (row, col, openings, treasureId|null)
  const FIXED = [
    [0, 0, E | S, null],
    [0, 2, E | S | W, "owl"],
    [0, 4, E | S | W, "map"],
    [0, 6, S | W, null],
    [2, 0, N | E | S, "gem"],
    [2, 2, N | E | S, "key"],
    [2, 4, E | S | W, "ring"],
    [2, 6, N | S | W, "book"],
    [4, 0, N | E | S, "crown"],
    [4, 2, N | E | W, "lantern"],
    [4, 4, N | S | W, "scroll"],
    [4, 6, N | S | W, "compass"],
    [6, 0, N | E, null],
    [6, 2, N | E | W, "chalice"],
    [6, 4, N | E | W, "amulet"],
    [6, 6, N | W, null],
  ];

  function rot(openings, turns) {
    const map = [N, E, S, W];
    let out = 0;
    const t = ((turns % 4) + 4) % 4;
    for (let i = 0; i < 4; i++) {
      if (openings & map[i]) out |= map[(i + t) % 4];
    }
    return out;
  }

  function tileKind(openings) {
    const bits = [N, E, S, W].filter((b) => openings & b).length;
    if (bits === 2) {
      if (openings === (N | S) || openings === (E | W)) return "I";
      return "L";
    }
    if (bits === 3) return "T";
    return "?";
  }

  function makeMovableDeck() {
    // 12 straight, 16 corner, 6 T with treasures on some T/corners for remaining treasures
    const deck = [];
    for (let i = 0; i < 12; i++) deck.push({ openings: N | S, treasure: null, fixed: false });
    for (let i = 0; i < 16; i++) deck.push({ openings: N | E, treasure: null, fixed: false });
    const movableTreasures = TREASURES.filter(
      (t) => !FIXED.some((f) => f[3] === t.id)
    );
    for (let i = 0; i < 6; i++) {
      deck.push({
        openings: N | E | W,
        treasure: movableTreasures[i] ? movableTreasures[i].id : null,
        fixed: false,
      });
    }
    // Remaining movable treasures on corners
    let ti = 6;
    for (let i = 0; i < deck.length && ti < movableTreasures.length; i++) {
      if (deck[i].treasure || tileKind(deck[i].openings) !== "L") continue;
      deck[i].treasure = movableTreasures[ti++].id;
    }
    return shuffle(deck).map((t) => ({
      ...t,
      openings: rot(t.openings, Math.floor(Math.random() * 4)),
    }));
  }

  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function treasureById(id) {
    return TREASURES.find((t) => t.id === id) || null;
  }

  function pushSlots() {
    const slots = [];
    for (const line of [1, 3, 5]) {
      slots.push({ edge: "n", line, r: -1, c: line });
      slots.push({ edge: "s", line, r: SIZE, c: line });
      slots.push({ edge: "w", line, r: line, c: -1 });
      slots.push({ edge: "e", line, r: line, c: SIZE });
    }
    return slots;
  }

  const state = {
    board: null,
    spare: null,
    players: [],
    turn: 0,
    phase: "insert", // insert | move
    lastPush: null,
    reachable: new Set(),
    winner: null,
  };

  const els = {
    setup: document.getElementById("setup"),
    play: document.getElementById("play"),
    topActions: document.getElementById("top-actions"),
    playerCount: document.getElementById("player-count"),
    btnStart: document.getElementById("btn-start"),
    btnNew: document.getElementById("btn-new"),
    btnRotate: document.getElementById("btn-rotate"),
    btnAgain: document.getElementById("btn-again"),
    turnName: document.getElementById("turn-name"),
    phase: document.getElementById("phase"),
    insertHint: document.getElementById("insert-hint"),
    treasureGlyph: document.getElementById("treasure-glyph"),
    treasureLabel: document.getElementById("treasure-label"),
    treasureLeft: document.getElementById("treasure-left"),
    scores: document.getElementById("scores"),
    board: document.getElementById("board"),
    spare: document.getElementById("spare"),
    winModal: document.getElementById("win-modal"),
    winTitle: document.getElementById("win-title"),
    winCopy: document.getElementById("win-copy"),
  };

  const bctx = els.board.getContext("2d");
  const sctx = els.spare.getContext("2d");
  els.board.width = BOARD_PX;
  els.board.height = BOARD_PX;

  function emptyBoard() {
    return Array.from({ length: SIZE }, () => Array(SIZE).fill(null));
  }

  function buildBoard() {
    const board = emptyBoard();
    for (const [r, c, openings, treasure] of FIXED) {
      board[r][c] = { openings, treasure, fixed: true };
    }
    const deck = makeMovableDeck();
    let i = 0;
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        if (board[r][c]) continue;
        board[r][c] = deck[i++];
      }
    }
    const spare = deck[i];
    return { board, spare };
  }

  function dealTreasures(count) {
    const ids = shuffle(TREASURES.map((t) => t.id));
    const per = Math.floor(ids.length / count);
    const hands = Array.from({ length: count }, () => []);
    let idx = 0;
    for (let p = 0; p < count; p++) {
      for (let n = 0; n < per; n++) hands[p].push(ids[idx++]);
    }
    return hands;
  }

  function startGame(count) {
    const { board, spare } = buildBoard();
    const hands = dealTreasures(count);
    state.board = board;
    state.spare = spare;
    state.players = PLAYERS.slice(0, count).map((p, i) => ({
      ...p,
      r: p.start[0],
      c: p.start[1],
      hand: hands[i],
      collected: 0,
    }));
    state.turn = 0;
    state.phase = "insert";
    state.lastPush = null;
    state.reachable = new Set();
    state.winner = null;
    els.setup.hidden = true;
    els.play.hidden = false;
    els.topActions.hidden = false;
    els.winModal.hidden = true;
    updateSidebar();
    draw();
  }

  function currentPlayer() {
    return state.players[state.turn];
  }

  function key(r, c) {
    return `${r},${c}`;
  }

  function openingsAt(r, c) {
    const t = state.board[r]?.[c];
    return t ? t.openings : 0;
  }

  function connected(r, c, nr, nc) {
    if (nr < 0 || nc < 0 || nr >= SIZE || nc >= SIZE) return false;
    const a = openingsAt(r, c);
    const b = openingsAt(nr, nc);
    if (nr === r - 1 && nc === c) return (a & N) && (b & S);
    if (nr === r + 1 && nc === c) return (a & S) && (b & N);
    if (nr === r && nc === c - 1) return (a & W) && (b & E);
    if (nr === r && nc === c + 1) return (a & E) && (b & W);
    return false;
  }

  function computeReachable(pr, pc) {
    const seen = new Set([key(pr, pc)]);
    const q = [[pr, pc]];
    while (q.length) {
      const [r, c] = q.shift();
      for (const [dr, dc] of [
        [-1, 0],
        [1, 0],
        [0, -1],
        [0, 1],
      ]) {
        const nr = r + dr;
        const nc = c + dc;
        const k = key(nr, nc);
        if (seen.has(k)) continue;
        if (!connected(r, c, nr, nc)) continue;
        seen.add(k);
        q.push([nr, nc]);
      }
    }
    return seen;
  }

  function oppositeEdge(edge) {
    return { n: "s", s: "n", w: "e", e: "w" }[edge];
  }

  function canPush(slot) {
    if (!state.lastPush) return true;
    return !(
      state.lastPush.line === slot.line &&
      oppositeEdge(state.lastPush.edge) === slot.edge
    );
  }

  function shiftPlayers(edge, line, incomingSpare) {
    const delta =
      edge === "n" || edge === "w"
        ? 1
        : edge === "s" || edge === "e"
          ? -1
          : 0;
    for (const p of state.players) {
      if (edge === "n" || edge === "s") {
        if (p.c !== line) continue;
        p.r += delta;
        if (p.r < 0) p.r = SIZE - 1;
        if (p.r >= SIZE) p.r = 0;
      } else {
        if (p.r !== line) continue;
        p.c += delta;
        if (p.c < 0) p.c = SIZE - 1;
        if (p.c >= SIZE) p.c = 0;
      }
    }
    // If a piece was on the ejected tile conceptually they wrap with the tile — already handled.
    void incomingSpare;
  }

  function pushTile(slot) {
    if (state.phase !== "insert" || !canPush(slot)) return;
    const { edge, line } = slot;
    let ejected;
    if (edge === "n") {
      ejected = state.board[SIZE - 1][line];
      for (let r = SIZE - 1; r > 0; r--) state.board[r][line] = state.board[r - 1][line];
      state.board[0][line] = state.spare;
    } else if (edge === "s") {
      ejected = state.board[0][line];
      for (let r = 0; r < SIZE - 1; r++) state.board[r][line] = state.board[r + 1][line];
      state.board[SIZE - 1][line] = state.spare;
    } else if (edge === "w") {
      ejected = state.board[line][SIZE - 1];
      for (let c = SIZE - 1; c > 0; c--) state.board[line][c] = state.board[line][c - 1];
      state.board[line][0] = state.spare;
    } else {
      ejected = state.board[line][0];
      for (let c = 0; c < SIZE - 1; c++) state.board[line][c] = state.board[line][c + 1];
      state.board[line][SIZE - 1] = state.spare;
    }
    shiftPlayers(edge, line, state.spare);
    state.spare = { ...ejected, fixed: false };
    state.lastPush = { edge, line };
    state.phase = "move";
    const p = currentPlayer();
    state.reachable = computeReachable(p.r, p.c);
    updateSidebar();
    draw();
  }

  function tryCollect(p) {
    const tile = state.board[p.r][p.c];
    if (!tile?.treasure) return;
    if (p.hand[0] !== tile.treasure) return;
    p.hand.shift();
    p.collected += 1;
    if (p.hand.length === 0) {
      state.winner = p;
      els.winTitle.textContent = `${p.name} wins!`;
      els.winCopy.textContent = `${p.name} collected every treasure in the maze.`;
      els.winModal.hidden = false;
    }
  }

  function moveTo(r, c) {
    if (state.phase !== "move") return;
    if (!state.reachable.has(key(r, c))) return;
    const p = currentPlayer();
    p.r = r;
    p.c = c;
    tryCollect(p);
    if (state.winner) {
      updateSidebar();
      draw();
      return;
    }
    state.turn = (state.turn + 1) % state.players.length;
    state.phase = "insert";
    state.reachable = new Set();
    updateSidebar();
    draw();
  }

  function updateSidebar() {
    const p = currentPlayer();
    els.turnName.textContent = p.name;
    els.turnName.style.color = p.color;
    if (state.phase === "insert") {
      els.phase.textContent = "Insert a tile";
      els.insertHint.textContent = "Rotate the extra tile, then tap a yellow arrow.";
    } else {
      els.phase.textContent = "Move your piece";
      els.insertHint.textContent = "Tap a highlighted path cell, or stay put by tapping your piece.";
    }
    const next = treasureById(p.hand[0]);
    if (next) {
      els.treasureGlyph.textContent = next.glyph;
      els.treasureLabel.textContent = next.label;
      els.treasureLeft.textContent = `${p.hand.length} treasure${p.hand.length === 1 ? "" : "s"} left`;
    } else {
      els.treasureGlyph.textContent = "✓";
      els.treasureLabel.textContent = "All found";
      els.treasureLeft.textContent = "0 left";
    }
    els.scores.innerHTML = state.players
      .map((pl) => {
        const active = pl.id === p.id ? " is-active" : "";
        return `<li class="${active}"><span><span class="swatch" style="background:${pl.color}"></span>${pl.name}</span><span>${pl.collected}/${pl.collected + pl.hand.length}</span></li>`;
      })
      .join("");
  }

  function cellOrigin(r, c) {
    return [PAD + c * CELL, PAD + r * CELL];
  }

  function drawPath(ctx, x, y, size, openings, opts = {}) {
    const { treasure, fixed, highlight } = opts;
    const g = size * 0.18;
    const path = size * 0.36;
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = fixed ? "#3f3429" : "#4a3d30";
    roundRect(ctx, 2, 2, size - 4, size - 4, 10);
    ctx.fill();
    if (highlight) {
      ctx.strokeStyle = "rgba(224, 179, 77, 0.95)";
      ctx.lineWidth = 3;
      roundRect(ctx, 3, 3, size - 6, size - 6, 10);
      ctx.stroke();
    }
    ctx.fillStyle = "#d7c4a3";
    const cx = size / 2;
    const cy = size / 2;
    ctx.fillRect(cx - path / 2, cy - path / 2, path, path);
    if (openings & N) ctx.fillRect(cx - path / 2, 2, path, cy);
    if (openings & S) ctx.fillRect(cx - path / 2, cy, path, size - 2 - cy);
    if (openings & W) ctx.fillRect(2, cy - path / 2, cx, path);
    if (openings & E) ctx.fillRect(cx, cy - path / 2, size - 2 - cx, path);
    if (treasure) {
      const t = treasureById(treasure);
      ctx.font = `${Math.floor(size * 0.34)}px serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(t ? t.glyph : "?", cx, cy);
    } else {
      ctx.fillStyle = "rgba(255,255,255,0.08)";
      ctx.beginPath();
      ctx.arc(cx, cy, g, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function roundRect(ctx, x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  function drawArrow(ctx, x, y, dir, enabled) {
    ctx.save();
    ctx.translate(x, y);
    const ang = { n: 0, e: Math.PI / 2, s: Math.PI, w: -Math.PI / 2 }[dir];
    ctx.rotate(ang);
    ctx.fillStyle = enabled ? "#e0b34d" : "#6b6256";
    ctx.beginPath();
    ctx.moveTo(0, -14);
    ctx.lineTo(12, 8);
    ctx.lineTo(-12, 8);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function draw() {
    const ctx = bctx;
    ctx.clearRect(0, 0, BOARD_PX, BOARD_PX);
    ctx.fillStyle = "#241e18";
    roundRect(ctx, 0, 0, BOARD_PX, BOARD_PX, 24);
    ctx.fill();

    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const [x, y] = cellOrigin(r, c);
        const tile = state.board[r][c];
        const highlight = state.phase === "move" && state.reachable.has(key(r, c));
        drawPath(ctx, x, y, CELL, tile.openings, {
          treasure: tile.treasure,
          fixed: tile.fixed,
          highlight,
        });
      }
    }

    for (const slot of pushSlots()) {
      const enabled = state.phase === "insert" && canPush(slot);
      let x;
      let y;
      if (slot.edge === "n") {
        x = PAD + slot.line * CELL + CELL / 2;
        y = PAD / 2;
      } else if (slot.edge === "s") {
        x = PAD + slot.line * CELL + CELL / 2;
        y = BOARD_PX - PAD / 2;
      } else if (slot.edge === "w") {
        x = PAD / 2;
        y = PAD + slot.line * CELL + CELL / 2;
      } else {
        x = BOARD_PX - PAD / 2;
        y = PAD + slot.line * CELL + CELL / 2;
      }
      drawArrow(ctx, x, y, slot.edge, enabled);
    }

    // players
    const groups = {};
    for (const p of state.players) {
      const k = key(p.r, p.c);
      (groups[k] ||= []).push(p);
    }
    for (const [k, list] of Object.entries(groups)) {
      const [r, c] = k.split(",").map(Number);
      const [x, y] = cellOrigin(r, c);
      list.forEach((p, i) => {
        const ox = (i - (list.length - 1) / 2) * 14;
        const cx = x + CELL / 2 + ox;
        const cy = y + CELL / 2 + (list.length > 1 ? 10 : 0);
        ctx.beginPath();
        ctx.fillStyle = p.color;
        ctx.strokeStyle = "#fff8ef";
        ctx.lineWidth = 3;
        ctx.arc(cx, cy, 12, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        if (p.id === currentPlayer().id) {
          ctx.strokeStyle = "#e0b34d";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(cx, cy, 16, 0, Math.PI * 2);
          ctx.stroke();
        }
      });
    }

    // spare
    sctx.clearRect(0, 0, 88, 88);
    drawPath(sctx, 4, 4, 80, state.spare.openings, {
      treasure: state.spare.treasure,
      fixed: false,
    });
  }

  function hitTest(mx, my) {
    const rect = els.board.getBoundingClientRect();
    const x = ((mx - rect.left) / rect.width) * BOARD_PX;
    const y = ((my - rect.top) / rect.height) * BOARD_PX;

    for (const slot of pushSlots()) {
      let sx;
      let sy;
      if (slot.edge === "n") {
        sx = PAD + slot.line * CELL + CELL / 2;
        sy = PAD / 2;
      } else if (slot.edge === "s") {
        sx = PAD + slot.line * CELL + CELL / 2;
        sy = BOARD_PX - PAD / 2;
      } else if (slot.edge === "w") {
        sx = PAD / 2;
        sy = PAD + slot.line * CELL + CELL / 2;
      } else {
        sx = BOARD_PX - PAD / 2;
        sy = PAD + slot.line * CELL + CELL / 2;
      }
      if ((x - sx) ** 2 + (y - sy) ** 2 <= 22 ** 2) return { type: "arrow", slot };
    }

    if (x >= PAD && y >= PAD && x < PAD + SIZE * CELL && y < PAD + SIZE * CELL) {
      const c = Math.floor((x - PAD) / CELL);
      const r = Math.floor((y - PAD) / CELL);
      return { type: "cell", r, c };
    }
    return null;
  }

  els.board.addEventListener("click", (event) => {
    if (state.winner) return;
    const hit = hitTest(event.clientX, event.clientY);
    if (!hit) return;
    if (hit.type === "arrow") {
      if (state.phase === "insert" && canPush(hit.slot)) pushTile(hit.slot);
      return;
    }
    if (hit.type === "cell" && state.phase === "move") moveTo(hit.r, hit.c);
  });

  els.btnRotate.addEventListener("click", () => {
    if (state.phase !== "insert" || state.winner) return;
    state.spare.openings = rot(state.spare.openings, 1);
    draw();
  });

  els.btnStart.addEventListener("click", () => {
    startGame(Number(els.playerCount.value));
  });

  function resetToSetup() {
    els.play.hidden = true;
    els.setup.hidden = false;
    els.topActions.hidden = true;
    els.winModal.hidden = true;
  }

  els.btnNew.addEventListener("click", resetToSetup);
  els.btnAgain.addEventListener("click", () => {
    startGame(state.players.length || 4);
  });
})();
