/* Display builder: drag-and-drop TRMNL screen layout over a live render.
 *
 * The preview is the real universal template rendered server-side with the
 * same payload a push sends (iframe srcdoc, sandboxed). An overlay grid on top
 * holds one handle per widget; pointer events move / resize them on the
 * device's cell grid. Every change re-renders the preview.
 */
(function () {
  const root = document.querySelector("[data-display-builder]");
  const dataEl = document.getElementById("builder-data");
  if (!root || !dataEl) return;

  let data;
  try {
    data = JSON.parse(dataEl.textContent || "{}");
  } catch {
    return;
  }

  const displayId = data.display.id;
  // Relative to /displays/<id> so it works with or without the /notify path prefix.
  const previewUrl = new URL(`../api/displays/${encodeURIComponent(displayId)}/preview`, window.location.href).href;
  const catalog = Object.fromEntries((data.catalog || []).map((w) => [w.id, w]));
  const SIZES = data.sizes || {};
  const SIZE_LABELS = { small: "Small", medium: "Medium", large: "Large", wide: "Wide" };
  const screens = data.screens || {};
  const csrf = root.querySelector('input[name="csrf_token"]')?.value || "";

  const el = {
    device: root.querySelector("[data-builder-device]"),
    titleBar: root.querySelector("[data-builder-title-bar]"),
    titleBarField: root.querySelector("[data-builder-title-bar-field]"),
    dateFormat: root.querySelector("[data-builder-date-format]"),
    dateFormatField: root.querySelector("[data-builder-date-format-field]"),
    dateWrap: root.querySelector("[data-builder-date-wrap]"),
    dataBtns: [...root.querySelectorAll("[data-builder-data]")],
    eink: root.querySelector("[data-builder-eink]"),
    tray: root.querySelector("[data-builder-tray]"),
    unplaced: root.querySelector("[data-builder-unplaced]"),
    unplacedList: root.querySelector("[data-builder-unplaced-list]"),
    stage: root.querySelector("[data-builder-stage]"),
    screen: root.querySelector("[data-device-screen]"),
    inner: root.querySelector("[data-device-inner]"),
    preview: root.querySelector("[data-builder-preview]"),
    overlay: root.querySelector("[data-builder-overlay]"),
    status: root.querySelector("[data-builder-status]"),
    inspector: root.querySelector("[data-builder-inspector]"),
    inspectorName: root.querySelector("[data-inspector-name]"),
    inspectorWhere: root.querySelector("[data-inspector-where]"),
    inspectorSizes: root.querySelector("[data-inspector-sizes]"),
    inspectorRemove: root.querySelector("[data-inspector-remove]"),
    form: root.querySelector("[data-builder-form]"),
    deviceField: root.querySelector("[data-builder-device-field]"),
    widgetsField: root.querySelector("[data-builder-widgets-field]"),
    reset: root.querySelector("[data-builder-reset]"),
    dirty: root.querySelector("[data-builder-dirty]"),
  };

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const saved = {
    device: data.display.device || "og",
    title_bar: data.display.title_bar || "bottom",
    date_format: data.display.date_format || "day_time",
    widgets: clone(data.display.widgets || []),
  };
  let state = clone(saved);
  let dataMode = "sample";
  let selectedId = null;
  let geometry = null; // .layout box inside the preview, in device px
  let previewSeq = 0;
  let previewTimer = 0;
  let version = 0; // bumps on every local edit; stale preview responses are dropped

  // ---- grid maths --------------------------------------------------------

  function screen() {
    return screens[state.device] || screens.og || { cols: 4, rows: 3, width: 800, height: 480 };
  }

  function footprint(size) {
    const [w, h] = SIZES[size] || SIZES.medium || [2, 1];
    const s = screen();
    return [Math.min(w, s.cols), Math.min(h, s.rows)];
  }

  function taken(exceptId) {
    const set = new Set();
    for (const w of state.widgets) {
      if (w.id === exceptId || !w.x || !w.y) continue;
      const [fw, fh] = footprint(w.size);
      for (let cx = w.x; cx < w.x + fw; cx += 1) {
        for (let cy = w.y; cy < w.y + fh; cy += 1) set.add(`${cx},${cy}`);
      }
    }
    return set;
  }

  function fits(x, y, w, h, exceptId) {
    const s = screen();
    if (x < 1 || y < 1 || x + w - 1 > s.cols || y + h - 1 > s.rows) return false;
    const used = taken(exceptId);
    for (let cx = x; cx < x + w; cx += 1) {
      for (let cy = y; cy < y + h; cy += 1) if (used.has(`${cx},${cy}`)) return false;
    }
    return true;
  }

  function firstFit(w, h, exceptId) {
    const s = screen();
    for (let y = 1; y <= s.rows; y += 1) {
      for (let x = 1; x <= s.cols; x += 1) if (fits(x, y, w, h, exceptId)) return [x, y];
    }
    return null;
  }

  function defaultSize(widgetId) {
    const sizes = catalog[widgetId]?.sizes || ["medium"];
    return sizes.includes("medium") ? "medium" : sizes[0];
  }

  function newId() {
    return Math.random().toString(16).slice(2, 10);
  }

  // Box of the layout area in device px (from the preview), falling back to an estimate.
  function layoutBox() {
    const s = screen();
    if (geometry && geometry.w > 0) return geometry;
    const pad = 16;
    return { x: pad, y: pad, w: s.width - pad * 2, h: s.height - pad * 2 - 44 };
  }

  const GAP = 8;

  function cellAt(clientX, clientY) {
    const rect = el.overlay.getBoundingClientRect();
    const s = screen();
    const colW = rect.width / s.cols;
    const rowH = rect.height / s.rows;
    return {
      col: Math.floor((clientX - rect.left) / colW) + 1,
      row: Math.floor((clientY - rect.top) / rowH) + 1,
      inside: clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom,
      colW,
      rowH,
    };
  }

  // ---- rendering ---------------------------------------------------------

  function applyScale() {
    const s = screen();
    el.screen.style.setProperty("--screen-w", String(s.width));
    el.screen.style.setProperty("--screen-h", String(s.height));
    const scale = el.screen.clientWidth / s.width || 1;
    el.inner.style.setProperty("--screen-scale", String(scale));
  }

  function positionOverlay() {
    const s = screen();
    const box = layoutBox();
    Object.assign(el.overlay.style, {
      left: `${box.x}px`,
      top: `${box.y}px`,
      width: `${box.w}px`,
      height: `${box.h}px`,
      gridTemplateColumns: `repeat(${s.cols}, minmax(0, 1fr))`,
      gridTemplateRows: `repeat(${s.rows}, minmax(0, 1fr))`,
      gap: `${GAP}px`,
    });
  }

  function describe(w) {
    const meta = catalog[w.widget_id];
    const where = w.x ? `column ${w.x}, row ${w.y}` : "not on screen";
    return `${meta?.label || w.widget_id}, ${SIZE_LABELS[w.size] || w.size}, ${where}`;
  }

  function renderOverlay() {
    const s = screen();
    el.overlay.replaceChildren();
    for (let y = 1; y <= s.rows; y += 1) {
      for (let x = 1; x <= s.cols; x += 1) {
        const cell = document.createElement("div");
        cell.className = "grid-cell";
        cell.style.gridArea = `${y} / ${x} / span 1 / span 1`;
        el.overlay.append(cell);
      }
    }
    for (const w of state.widgets) {
      if (!w.x || !w.y) continue;
      const [fw, fh] = footprint(w.size);
      const handle = document.createElement("div");
      handle.className = "grid-widget";
      handle.tabIndex = 0;
      handle.setAttribute("role", "button");
      handle.setAttribute("aria-label", describe(w));
      handle.dataset.widget = w.id;
      handle.style.gridArea = `${w.y} / ${w.x} / span ${fh} / span ${fw}`;
      if (w.id === selectedId) {
        handle.classList.add("is-selected");
        handle.setAttribute("aria-pressed", "true");
      }
      const grip = document.createElement("span");
      grip.className = "grid-resize";
      grip.dataset.resize = w.id;
      grip.setAttribute("aria-hidden", "true");
      handle.append(grip);
      el.overlay.append(handle);
    }
  }

  function renderTray() {
    const unplaced = state.widgets.filter((w) => !w.x || !w.y);
    el.unplaced.hidden = unplaced.length === 0;
    el.unplacedList.replaceChildren(
      ...unplaced.map((w) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tray-item is-unplaced";
        btn.dataset.unplaced = w.id;
        btn.innerHTML = `<span class="tray-item-label"></span><span class="tray-item-app">Drag in or tap to remove</span>`;
        btn.querySelector(".tray-item-label").textContent = catalog[w.widget_id]?.label || w.widget_id;
        li.append(btn);
        return li;
      }),
    );
  }

  function renderInspector() {
    const w = state.widgets.find((item) => item.id === selectedId);
    el.inspector.hidden = !w;
    if (!w) return;
    const meta = catalog[w.widget_id] || { sizes: [w.size], label: w.widget_id };
    el.inspectorName.textContent = meta.label;
    el.inspectorWhere.textContent = w.x ? `Column ${w.x}, row ${w.y}` : "Not on screen";
    el.inspectorSizes.replaceChildren(
      ...meta.sizes.map((size) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "seg-btn";
        btn.dataset.size = size;
        btn.textContent = SIZE_LABELS[size] || size;
        btn.setAttribute("aria-pressed", String(size === w.size));
        const [fw, fh] = footprint(size);
        const ok = size === w.size || (w.x ? fits(w.x, w.y, fw, fh, w.id) || !!firstFit(fw, fh, w.id) : true);
        btn.disabled = !ok;
        if (!ok) btn.title = "No room for this size";
        return btn;
      }),
    );
  }

  function markDirty() {
    const changed = JSON.stringify(strip(state)) !== JSON.stringify(strip(saved));
    el.dirty.hidden = !changed;
    el.reset.disabled = !changed;
    el.deviceField.value = state.device;
    el.titleBarField.value = state.title_bar;
    el.dateFormatField.value = state.date_format;
    el.dateWrap.hidden = state.title_bar === "off";
    el.widgetsField.value = JSON.stringify(state.widgets);
    return changed;
  }

  function strip(s) {
    return {
      device: s.device,
      title_bar: s.title_bar,
      date_format: s.date_format,
      widgets: s.widgets.map((w) => [w.id, w.widget_id, w.size, w.x || 0, w.y || 0]),
    };
  }

  function render() {
    const focusedId = document.activeElement?.closest?.("[data-widget]")?.dataset.widget;
    applyScale();
    positionOverlay();
    renderOverlay();
    if (focusedId) el.overlay.querySelector(`[data-widget="${CSS.escape(focusedId)}"]`)?.focus();
    renderTray();
    renderInspector();
    markDirty();
  }

  function focusWidget(id) {
    requestAnimationFrame(() => el.overlay.querySelector(`[data-widget="${CSS.escape(id)}"]`)?.focus());
  }

  // ---- preview -----------------------------------------------------------

  function setStatus(text) {
    el.status.textContent = text;
  }

  function schedulePreview(delay = 150) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(loadPreview, delay);
  }

  async function loadPreview() {
    const seq = ++previewSeq;
    const sentVersion = version;
    try {
      const response = await window.fetch(previewUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-StonePi-CSRF": csrf },
        credentials: "same-origin",
        body: JSON.stringify({ device: state.device, title_bar: state.title_bar, date_format: state.date_format, widgets: state.widgets, data: dataMode }),
      });
      const body = await response.json().catch(() => ({}));
      if (seq !== previewSeq || sentVersion !== version) return;
      if (!response.ok || !body.ok) {
        setStatus(body.message || `Preview failed (HTTP ${response.status}).`);
        return;
      }
      // Server placement is authoritative (auto-places anything without a spot).
      if (Array.isArray(body.widgets)) {
        state.widgets = body.widgets;
        render();
      }
      el.preview.srcdoc = body.html;
      const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
      const over = body.bytes > body.limit;
      const source = dataMode === "live" ? "Live data" : "Sample data";
      const unplaced = (body.unplaced || []).length;
      setStatus(
        `${source} · push size ${kb(body.bytes)} of ${kb(body.limit)}${over ? " — trimmed to fit" : ""}` +
          (unplaced ? ` · ${unplaced} widget${unplaced === 1 ? "" : "s"} not on screen` : ""),
      );
    } catch {
      if (seq === previewSeq) setStatus("Couldn't reach Notify to render the preview.");
    }
  }

  window.addEventListener("message", (event) => {
    if (event.source !== el.preview.contentWindow) return;
    const msg = event.data;
    if (!msg || msg.type !== "stonepi-preview-geometry") return;
    geometry = { x: msg.x, y: msg.y, w: msg.w, h: msg.h };
    positionOverlay();
  });

  // ---- mutations ---------------------------------------------------------

  function commit({ preview = true } = {}) {
    version += 1;
    render();
    if (preview) schedulePreview();
  }

  function addWidget(widgetId, at) {
    const size = defaultSize(widgetId);
    const [fw, fh] = footprint(size);
    let spot = at && fits(at[0], at[1], fw, fh) ? at : firstFit(fw, fh);
    const widget = { id: newId(), widget_id: widgetId, size, x: spot ? spot[0] : 0, y: spot ? spot[1] : 0, config: {} };
    if (!spot) {
      // Try the smallest allowed size before giving up.
      for (const alt of catalog[widgetId]?.sizes || []) {
        const [aw, ah] = footprint(alt);
        spot = firstFit(aw, ah);
        if (spot) {
          Object.assign(widget, { size: alt, x: spot[0], y: spot[1] });
          break;
        }
      }
    }
    state.widgets.push(widget);
    selectedId = widget.id;
    commit();
    if (!spot) setStatus(`No free space for ${catalog[widgetId]?.label || "that widget"} — it's listed under Not on screen.`);
    focusWidget(widget.id);
  }

  function removeWidget(id) {
    state.widgets = state.widgets.filter((w) => w.id !== id);
    if (selectedId === id) selectedId = null;
    commit();
  }

  function resizeWidget(id, size) {
    const w = state.widgets.find((item) => item.id === id);
    if (!w || w.size === size) return;
    const [fw, fh] = footprint(size);
    if (w.x && fits(w.x, w.y, fw, fh, id)) {
      w.size = size;
    } else {
      const spot = firstFit(fw, fh, id);
      if (!spot) return;
      Object.assign(w, { size, x: spot[0], y: spot[1] });
    }
    commit();
    focusWidget(id);
  }

  function moveWidget(id, x, y) {
    const w = state.widgets.find((item) => item.id === id);
    if (!w) return false;
    const [fw, fh] = footprint(w.size);
    if (!fits(x, y, fw, fh, id)) return false;
    w.x = x;
    w.y = y;
    commit();
    return true;
  }

  // ---- pointer drag ------------------------------------------------------

  let drag = null;

  function ghost(x, y, w, h, ok) {
    let g = el.overlay.querySelector(".grid-ghost");
    if (!g) {
      g = document.createElement("div");
      g.className = "grid-ghost";
      el.overlay.append(g);
    }
    g.classList.toggle("is-invalid", !ok);
    g.style.gridArea = `${y} / ${x} / span ${h} / span ${w}`;
    g.hidden = false;
  }

  function clearGhost() {
    el.overlay.querySelector(".grid-ghost")?.remove();
  }

  function chip(label) {
    const c = document.createElement("div");
    c.className = "drag-chip";
    c.textContent = label;
    document.body.append(c);
    return c;
  }

  function startDrag(event, kind, payload) {
    if (event.button !== undefined && event.button !== 0) return;
    drag = { kind, ...payload, startX: event.clientX, startY: event.clientY, moved: false, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function dragTarget(event) {
    const s = screen();
    const cell = cellAt(event.clientX, event.clientY);
    if (drag.kind === "resize") {
      const w = state.widgets.find((item) => item.id === drag.id);
      const wantW = Math.max(1, cell.col - w.x + 1);
      const wantH = Math.max(1, cell.row - w.y + 1);
      const sizes = catalog[w.widget_id]?.sizes || [w.size];
      let best = null;
      for (const size of sizes) {
        const [fw, fh] = footprint(size);
        const score = Math.abs(fw - wantW) + Math.abs(fh - wantH);
        if (!best || score < best.score) best = { size, fw, fh, score };
      }
      return { x: w.x, y: w.y, w: best.fw, h: best.fh, size: best.size, ok: fits(w.x, w.y, best.fw, best.fh, w.id), inside: true };
    }
    const size = drag.kind === "move" ? drag.size : defaultSize(drag.widgetId);
    const [fw, fh] = footprint(size);
    const x = Math.min(Math.max(1, cell.col - (drag.grabCol || 0)), s.cols - fw + 1);
    const y = Math.min(Math.max(1, cell.row - (drag.grabRow || 0)), s.rows - fh + 1);
    return { x, y, w: fw, h: fh, size, ok: fits(x, y, fw, fh, drag.id), inside: cell.inside };
  }

  function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 6) return;
    if (!drag.moved) {
      drag.moved = true;
      el.overlay.classList.add("is-dragging");
      if (drag.kind !== "resize") drag.chip = chip(drag.label);
      if (drag.kind === "move") el.overlay.querySelector(`[data-widget="${CSS.escape(drag.id)}"]`)?.classList.add("is-dragging");
    }
    event.preventDefault();
    if (drag.chip) {
      drag.chip.style.left = `${event.clientX + 12}px`;
      drag.chip.style.top = `${event.clientY + 12}px`;
    }
    const t = dragTarget(event);
    drag.target = t;
    if (t.inside) ghost(t.x, t.y, t.w, t.h, t.ok);
    else clearGhost();
  }

  function onPointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const d = drag;
    drag = null;
    d.chip?.remove();
    clearGhost();
    el.overlay.classList.remove("is-dragging");
    if (!d.moved) {
      // A tap: select a placed widget, add a tray widget, or drop an unplaced one.
      if (d.kind === "move") {
        selectedId = d.id;
        render();
        focusWidget(d.id);
      } else if (d.kind === "add") {
        addWidget(d.widgetId);
      } else if (d.kind === "unplaced") {
        removeWidget(d.id);
      }
      return;
    }
    const t = d.target;
    if (!t || !t.inside || !t.ok) {
      render();
      if (t && t.inside && !t.ok) setStatus("That space is taken — try an empty spot.");
      return;
    }
    if (d.kind === "resize") {
      const w = state.widgets.find((item) => item.id === d.id);
      w.size = t.size;
      commit();
    } else if (d.kind === "add") {
      addWidget(d.widgetId, [t.x, t.y]);
    } else {
      const w = state.widgets.find((item) => item.id === d.id);
      Object.assign(w, { x: t.x, y: t.y, size: t.size });
      selectedId = d.id;
      commit();
      focusWidget(d.id);
    }
  }

  el.overlay.addEventListener("pointerdown", (event) => {
    const grip = event.target.closest("[data-resize]");
    if (grip) {
      event.stopPropagation();
      startDrag(event, "resize", { id: grip.dataset.resize });
      return;
    }
    const handle = event.target.closest("[data-widget]");
    if (!handle) {
      if (selectedId) {
        selectedId = null;
        render();
      }
      return;
    }
    const w = state.widgets.find((item) => item.id === handle.dataset.widget);
    const cell = cellAt(event.clientX, event.clientY);
    startDrag(event, "move", {
      id: w.id,
      size: w.size,
      label: catalog[w.widget_id]?.label || w.widget_id,
      grabCol: cell.col - w.x,
      grabRow: cell.row - w.y,
    });
  });

  el.tray.addEventListener("pointerdown", (event) => {
    const btn = event.target.closest("[data-tray-widget]");
    if (!btn) return;
    const id = btn.dataset.trayWidget;
    startDrag(event, "add", { widgetId: id, label: catalog[id]?.label || id, grabCol: 0, grabRow: 0 });
  });

  el.unplacedList.addEventListener("pointerdown", (event) => {
    const btn = event.target.closest("[data-unplaced]");
    if (!btn) return;
    const w = state.widgets.find((item) => item.id === btn.dataset.unplaced);
    startDrag(event, "unplaced", { id: w.id, size: w.size, label: catalog[w.widget_id]?.label || w.widget_id, grabCol: 0, grabRow: 0 });
  });

  // Unplaced drops behave like moves once dragged.
  const baseUp = onPointerUp;
  function onUp(event) {
    if (drag && drag.kind === "unplaced" && drag.moved) drag.kind = "move";
    baseUp(event);
  }

  window.addEventListener("pointermove", onPointerMove, { passive: false });
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", () => {
    drag?.chip?.remove();
    drag = null;
    clearGhost();
    el.overlay.classList.remove("is-dragging");
  });

  // Keyboard clicks on tray buttons (pointer taps are handled above).
  el.tray.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-tray-widget]");
    if (btn && event.detail === 0) addWidget(btn.dataset.trayWidget);
  });
  el.unplacedList.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-unplaced]");
    if (btn && event.detail === 0) removeWidget(btn.dataset.unplaced);
  });

  // ---- keyboard on the canvas -------------------------------------------

  el.overlay.addEventListener("keydown", (event) => {
    const handle = event.target.closest("[data-widget]");
    if (!handle) return;
    const w = state.widgets.find((item) => item.id === handle.dataset.widget);
    if (!w) return;
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (moves[event.key]) {
      event.preventDefault();
      const [dx, dy] = moves[event.key];
      selectedId = w.id;
      if (!moveWidget(w.id, w.x + dx, w.y + dy)) setStatus("Can't move there.");
      focusWidget(w.id);
    } else if (event.key === "+" || event.key === "=" || event.key === "-") {
      event.preventDefault();
      const sizes = catalog[w.widget_id]?.sizes || [w.size];
      const i = sizes.indexOf(w.size) + (event.key === "-" ? -1 : 1);
      if (i >= 0 && i < sizes.length) resizeWidget(w.id, sizes[i]);
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      removeWidget(w.id);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectedId = w.id;
      render();
      focusWidget(w.id);
    }
  });

  // ---- inspector + controls ---------------------------------------------

  el.inspectorSizes.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-size]");
    if (btn && selectedId) resizeWidget(selectedId, btn.dataset.size);
  });
  el.inspectorRemove.addEventListener("click", () => selectedId && removeWidget(selectedId));

  el.device.addEventListener("change", () => {
    state.device = el.device.value;
    geometry = null;
    commit();
  });

  el.dateFormat.addEventListener("change", () => {
    state.date_format = el.dateFormat.value;
    commit();
  });

  // Title bar position changes the widget area, so the overlay waits for new geometry.
  el.titleBar.addEventListener("change", () => {
    state.title_bar = el.titleBar.value;
    geometry = null;
    commit();
  });

  for (const btn of el.dataBtns) {
    btn.addEventListener("click", () => {
      dataMode = btn.dataset.builderData;
      el.dataBtns.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      setStatus(dataMode === "live" ? "Collecting live data…" : "Rendering…");
      schedulePreview(0);
    });
  }

  const applyEink = () => el.screen.classList.toggle("is-eink", !!el.eink?.checked);
  el.eink?.addEventListener("change", applyEink);

  el.reset.addEventListener("click", () => {
    state = clone(saved);
    el.device.value = state.device;
    el.titleBar.value = state.title_bar;
    el.dateFormat.value = state.date_format;
    selectedId = null;
    geometry = null;
    commit();
  });

  let saving = false;
  el.form.addEventListener("submit", () => {
    markDirty();
    saving = true;
  });

  window.addEventListener("beforeunload", (event) => {
    if (!saving && markDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  if ("ResizeObserver" in window) new ResizeObserver(() => applyScale()).observe(el.screen);

  // ---- template copy -----------------------------------------------------

  document.querySelector("[data-copy-template]")?.addEventListener("click", async (event) => {
    const text = document.querySelector("[data-template-text]");
    const btn = event.currentTarget;
    try {
      await navigator.clipboard.writeText(text.value);
    } catch {
      text.select();
      document.execCommand?.("copy");
    }
    const label = btn.lastChild;
    const before = label.textContent;
    label.textContent = " Copied";
    setTimeout(() => (label.textContent = before), 1600);
  });

  applyEink();
  render();
  schedulePreview(0);
})();
