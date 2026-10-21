/* Displays → Car Thing editor: binds the form to one config, previews the unsaved draft in
   the drawing's screen (Notify proxies the real panel), and saves via JSON. */
(() => {
  const root = document.querySelector("[data-ct-editor]");
  const dataEl = document.getElementById("ct-data");
  if (!root || !dataEl) return;

  const data = JSON.parse(dataEl.textContent || "{}");
  const cat = data.catalog;
  const clone = (v) => JSON.parse(JSON.stringify(v));
  let saved = clone(data.config);
  let config = clone(data.config);
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || "";
  // Relative to /displays/carthing, so it works with or without the /notify prefix.
  const api = (path) => new URL(`../api/carthing/${path}`, window.location.href).href;
  const frame = root.querySelector("[data-ct-frame]");
  const actionsById = Object.fromEntries(cat.actions.map((a) => [a.id, a]));
  const miniById = Object.fromEntries(cat.mini_apps.map((a) => [a.id, a]));
  const widgetById = Object.fromEntries(cat.widgets.map((w) => [w.id, w]));
  const sizeCost = Object.fromEntries(cat.widget_sizes.map((z) => [z.id, z.cost]));
  let tab = "pages";
  let pageSel = 0; // the page being edited = the page the preview shows
  const frameBase = frame.getAttribute("src");

  // ── paths ────────────────────────────────────────────────────────────
  const get = (path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), config);
  const set = (path, value) => {
    const keys = path.split(".");
    const last = keys.pop();
    keys.reduce((o, k) => o[k], config)[last] = value;
  };

  // ── preview ──────────────────────────────────────────────────────────
  const send = (type, extra = {}) => {
    frame.contentWindow?.postMessage({ source: "stonepi-carthing", type, ...extra }, window.location.origin);
  };
  let draftTimer = null;
  const pushDraft = () => {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(async () => {
      try {
        const res = await fetch(api("draft"), {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-StonePi-CSRF": csrf },
          body: JSON.stringify(config),
        });
        if (res.ok) showPage();
      } catch {
        /* preview only */
      }
    }, 350);
  };
  // Reload the preview on the page being edited (its own page flips report back below).
  // After a screensaver change the preview opens straight on the screensaver (?saver=1), so it
  // shows as soon as the panel script runs instead of after every font and icon has loaded.
  let saverNext = false;
  let musicNext = false;
  const showPage = () => {
    const saver = saverNext ? "&saver=1" : "";
    saverNext = false;
    frame.src = `${frameBase}?page=${pageSel + 1}${saver}&r=${Date.now()}`;
  };
  window.addEventListener("message", (e) => {
    if (e.source !== frame.contentWindow || !e.data || e.data.source !== "stonepi-carthing-panel") return;
    if (e.data.type === "page" && Number.isInteger(e.data.page) && e.data.page < config.pages.length) {
      pageSel = e.data.page;
      renderPages();
    }
  });
  const fitScreen = () => {
    const box = root.querySelector("[data-ct-screen]");
    if (box) frame.style.transform = `scale(${box.clientWidth / cat.screen.width})`;
  };
  new ResizeObserver(fitScreen).observe(root.querySelector("[data-ct-screen]"));
  fitScreen();

  // ── dirty / save ─────────────────────────────────────────────────────
  const dirtyEl = root.querySelector("[data-ct-dirty]");
  const resetBtn = root.querySelector("[data-ct-reset]");
  const isDirty = () => JSON.stringify(config) !== JSON.stringify(saved);
  const useBtn = root.querySelector("[data-ct-use]");
  const changed = () => {
    const dirty = isDirty();
    dirtyEl.hidden = !dirty;
    dirtyEl.textContent = "Unsaved changes";
    resetBtn.disabled = !dirty;
    if (useBtn) {
      // Use on panel puts the saved config there, so save first.
      useBtn.disabled = dirty;
      useBtn.title = dirty ? "Save first" : "Put this config on the Car Thing";
    }
    refreshSummaries();
    pushDraft();
  };
  root.querySelector("[data-ct-save]").addEventListener("click", async () => {
    try {
      const res = await fetch(api(`config/${encodeURIComponent(config.id)}`), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-StonePi-CSRF": csrf },
        body: JSON.stringify(config),
      });
      const body = await res.json();
      if (!res.ok || !body.ok) throw new Error(body.message || "Couldn't save.");
      saved = clone(body.config);
      config = clone(body.config);
      pageSel = Math.min(pageSel, config.pages.length - 1);
      renderAll();
      dirtyEl.hidden = false;
      dirtyEl.textContent = "Saved";
      resetBtn.disabled = true;
      pushDraft();
    } catch (err) {
      dirtyEl.hidden = false;
      dirtyEl.textContent = err.message || "Couldn't save.";
    }
  });
  resetBtn.addEventListener("click", () => {
    config = clone(saved);
    pageSel = Math.min(pageSel, config.pages.length - 1);
    renderAll();
    changed();
  });
  guardLeave(isDirty, { body: "Your Car Thing changes aren't saved.", scope: root });

  // ── tabs ─────────────────────────────────────────────────────────────
  // The open tab is remembered, and #pages, #buttons, #idle or #look opens one directly.
  const tabs = root.querySelectorAll("[data-ct-tab]");
  const TAB_HASH = { pages: "pages", controls: "buttons", idle: "idle", look: "look" };
  const HASH_TAB = { pages: "pages", buttons: "controls", controls: "controls", idle: "idle", look: "look" };
  const TAB_KEY = "stonepi-carthing-tab";
  const setTab = (name, remember = true) => {
    if (!TAB_HASH[name]) name = "pages";
    if (remember) {
      try {
        localStorage.setItem(TAB_KEY, name);
      } catch {
        /* private mode: just not remembered */
      }
      history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${TAB_HASH[name]}`);
    }
    tab = name;
    tabs.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.ctTab === name)));
    root.querySelectorAll("[data-ct-pane]").forEach((p) => (p.hidden = p.dataset.ctPane !== name));
    root.querySelector("[data-ct-device]").classList.toggle("is-mapping", name === "controls");
    root.querySelector("[data-ct-device-hint]").textContent =
      name === "controls"
        ? "Click a button, a part of the dial, or a swipe arrow on the screen to choose what it does."
        : "Click the buttons, click or scroll over the dial, and tap the screen to try the panel. Hold a button for its long-press. The drawing is a schematic.";
  };
  tabs.forEach((b) => b.addEventListener("click", () => setTab(b.dataset.ctTab)));
  root.querySelectorAll("[data-ct-goto]").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      setTab(a.dataset.ctGoto);
      root.querySelector(`[data-ct-pane="${a.dataset.ctGoto}"]`).scrollIntoView({ block: "nearest" });
    }),
  );

  // ── screensaver style: each style's options; background and clock extras where they apply ──
  const renderSaver = () => {
    const style = config.idle.saver.style;
    root.querySelectorAll("[data-ct-saver-pane]").forEach((el) => {
      el.hidden = !el.dataset.ctSaverPane.split(" ").includes(style);
    });
    // Screensaver clock (U13): its own face and colours only while it's set apart.
    const separate = !!config.clock.saver.separate;
    root.querySelector("[data-ct-saver-clock]").hidden = !separate;
    // What the current screensaver style shows of this clock, so the preview never looks wrong.
    const note = root.querySelector("[data-ct-saver-clock-note]");
    const styleName = (cat.saver_styles.find((s) => s.id === style) || { label: style }).label;
    const timeOn = !!(config.idle.saver[style] || {}).time;
    // Shown whether or not the clock is set apart: an animated style shows the face only with Show time on.
    const text = style !== "clock" && !timeOn ? `${styleName} shows no clock. Turn on its Show time, or choose the Clock style, to see the face.` : "";
    note.textContent = text;
    note.hidden = !text;
    root.querySelector("[data-ct-face-hint]").textContent = separate
      ? "For the Clock and Clock & weather widgets only: the screensaver has its own face and colours (Idle → Screensaver clock)."
      : "For the Clock and Clock & weather widgets and the screensaver's clock (the Clock style, and Show time on the others).";
  };

  // ── simple bindings ──────────────────────────────────────────────────
  const binds = root.querySelectorAll("[data-bind]");
  const renderBinds = () => {
    binds.forEach((el) => {
      const value = get(el.dataset.bind);
      if (el.type === "checkbox") el.checked = !!value;
      else if (el.type === "radio") el.checked = el.value === value;
      else if (el.type === "color") el.value = (value || "#000000").toLowerCase();
      else el.value = value ?? "";
    });
    root.querySelectorAll("[data-ct-out]").forEach((o) => (o.textContent = `${get(o.dataset.ctOut)}%`));
  };
  binds.forEach((el) => {
    el.addEventListener(el.type === "range" || el.type === "color" ? "input" : "change", () => {
      let value;
      if (el.type === "checkbox") value = el.checked;
      else if (el.type === "radio") {
        if (!el.checked) return;
        value = el.value;
      } else if (el.type === "number" || el.type === "range" || el.dataset.bindNumber !== undefined) value = Number(el.value);
      else if (el.type === "color") value = el.value.toUpperCase();
      else value = el.value;
      set(el.dataset.bind, value);
      root.querySelectorAll("[data-ct-out]").forEach((o) => (o.textContent = `${get(o.dataset.ctOut)}%`));
      if (el.dataset.bind === "clock.color" || el.dataset.bind === "clock.face") renderSwatches();
      if (el.dataset.bind === "clock.saver.separate" && value) {
        // Start the screensaver's own clock from what the widgets show now.
        Object.assign(config.clock.saver, { face: config.clock.face, color: config.clock.color, accent: config.clock.accent });
        renderBinds();
      }
      if (el.dataset.bind.startsWith("header.")) renderHeaders();
      if (el.dataset.bind.startsWith("idle.saver.") || el.dataset.bind.startsWith("clock.saver.")) {
        renderSaver();
        saverNext = true; // show the screensaver once the preview reloads with the draft
      } else if (el.dataset.bind.startsWith("clock.")) {
        widgetClockChanged();
      } else if (el.dataset.bind.startsWith("music.np.")) {
        musicNext = true; // open Now Playing once the preview reloads with the draft
      } else if (el.dataset.bind.startsWith("music.saver_time.")) {
        saverNext = true; // the artwork screensaver (shows while music plays)
      }
      changed();
    });
  });
  // A Look → Clock change the screensaver shows too (no clock of its own): preview it on the
  // screensaver, unless the page being edited has a Clock widget to show it on.
  const saverShowsClock = () => {
    const saver = config.idle.saver;
    if (saver.style === "clock") return true;
    return !!(saver[saver.style] || {}).time;
  };
  const widgetClockChanged = () => {
    if (config.clock.saver.separate || !saverShowsClock()) return;
    if (config.pages[pageSel].widgets.some((w) => w.id === "clock" || w.id === "time")) return;
    saverNext = true;
  };

  // ── pages ────────────────────────────────────────────────────────────
  // Page pills (pick, drag or Alt + arrows to reorder, + to add), then the selected page:
  // name, header, the space bar and its widgets as rows (drag, size, settings, remove).
  const pagesEl = root.querySelector("[data-ct-pages]");
  const pageCount = root.querySelector("[data-ct-page-count]");
  const pageName = root.querySelector("[data-ct-page-name]");
  const pageDel = root.querySelector("[data-ct-page-del]");
  const pageSpace = root.querySelector("[data-ct-page-space]");
  const spaceBar = root.querySelector("[data-ct-space]");
  const spaceN = root.querySelector("[data-ct-space-n]");
  const widgetsEl = root.querySelector("[data-ct-page-widgets]");
  const addWidget = root.querySelector("[data-ct-add-widget]");
  const addFull = root.querySelector("[data-ct-add-full]");
  const webAddPage = root.querySelector('[data-ct-web-add="page"]');
  const turnText = root.querySelector("[data-ct-turn-text]");
  const used = (widgets) => widgets.reduce((n, w) => n + sizeCost[w.size], 0);
  const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const TINTS = ["sand", "sky", "moss", "amethyst", "ember", "rust"];
  const gearIcon = document.querySelector(".ct-title-row .ct-chip svg")?.outerHTML || "⚙";
  const doneIcon = root.querySelector("[data-ct-save] svg")?.outerHTML || "✓";
  const pagesChanged = () => {
    renderPages();
    renderControls(); // "Go to page N" labels follow page names
    renderAllowed();
    changed();
  };
  const selectPage = (i) => {
    pageSel = (i + config.pages.length) % config.pages.length;
    renderPages();
    showPage();
  };

  // Clock and Music settings live under Look (they also drive the screensaver clock and the
  // preset buttons); an open Clock or Music widget row borrows them.
  const boxes = { clock: root.querySelector('[data-ct-box="clock"]'), music: root.querySelector('[data-ct-box="music"]') };
  const homes = { clock: root.querySelector('[data-ct-home="clock"]'), music: root.querySelector('[data-ct-home="music"]') };
  const boxFor = (id) => (id === "clock" || id === "time" ? "clock" : id === "music" ? "music" : "");
  let openRow = ""; // the widget whose settings are open on its row
  const sendHome = () => {
    Object.keys(boxes).forEach((k) => {
      if (boxes[k].parentNode !== homes[k]) homes[k].appendChild(boxes[k]);
      homes[k].querySelector("[data-ct-away]").hidden = true;
    });
  };
  const faceLabel = (id) => (cat.clock_faces.find((f) => f.id === id) || { label: id }).label;
  const summary = (id) => {
    if (id === "clock" || id === "time") return `${faceLabel(config.clock.face)} · ${config.clock.hour_format === "12" ? "12-hour" : "24-hour"}`;
    if (id === "weather") return config.weather.lat != null ? config.weather.label || "Weather" : "No location yet: set it under Look";
    if (id === "music") {
      const n = config.music.presets.filter((p) => p.uri).length;
      return `${n} preset${n === 1 ? "" : "s"} · dial volume ${config.music.dial_volume ? "on" : "off"}`;
    }
    if (widgetById[id]?.source === "web") return "Web page · full page only";
    if (!widgetById[id]) return "Its web page was deleted: take it off";
    return miniById[id] ? `Opens ${miniById[id].label}` : "";
  };

  // Reorder by dragging. A mouse uses drag and drop (from the grip, for rows); touch and pens
  // follow the finger, because browsers fire no drag events for touch.
  const sortable = (list, itemSel, handleSel, onMove) => {
    let from = -1;
    let over = -1;
    const items = () => Array.from(list.querySelectorAll(itemSel));
    const mark = (i) => {
      over = i;
      items().forEach((el, n) => el.classList.toggle("is-drop", n === i && i !== from));
    };
    const finish = () => {
      const f = from;
      const t = over;
      from = -1;
      over = -1;
      items().forEach((el) => el.classList.remove("is-drag", "is-drop"));
      if (f >= 0 && t >= 0 && t !== f) onMove(f, t);
    };
    list.addEventListener("dragstart", (e) => {
      const el = e.target.closest?.(itemSel);
      if (!el || (handleSel && !el.draggable)) return;
      from = items().indexOf(el);
      el.classList.add("is-drag");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(from));
    });
    list.addEventListener("dragover", (e) => {
      const el = from >= 0 && e.target.closest?.(itemSel);
      if (!el) return;
      e.preventDefault();
      mark(items().indexOf(el));
    });
    list.addEventListener("drop", (e) => {
      if (from < 0) return;
      e.preventDefault();
      finish();
    });
    list.addEventListener("dragend", () => {
      if (from >= 0) {
        over = -1;
        finish();
      }
      if (handleSel) items().forEach((el) => (el.draggable = false));
    });
    list.addEventListener("pointerdown", (e) => {
      const start = handleSel ? e.target.closest(handleSel) : e.target.closest(itemSel);
      const el = start && start.closest(itemSel);
      if (!el) return;
      if (e.pointerType === "mouse") {
        if (handleSel) el.draggable = true; // a row drags from its grip only
        return;
      }
      if (handleSel) e.preventDefault();
      from = items().indexOf(el);
      const x0 = e.clientX;
      const y0 = e.clientY;
      let moving = false;
      const move = (ev) => {
        if (!moving && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) return;
        moving = true;
        el.classList.add("is-drag");
        const hit = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(itemSel);
        if (hit && list.contains(hit)) mark(items().indexOf(hit));
      };
      const end = (ev) => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", end);
        document.removeEventListener("pointercancel", end);
        if (moving && ev.type === "pointerup") finish();
        else {
          from = -1;
          items().forEach((n) => n.classList.remove("is-drag", "is-drop"));
        }
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", end);
      document.addEventListener("pointercancel", end);
    });
    list.addEventListener("pointerup", () => {
      if (handleSel && from < 0) items().forEach((el) => (el.draggable = false));
    });
  };
  const movePage = (from, to) => {
    const pages = config.pages;
    if (to < 0 || to >= pages.length || to === from) return;
    const [page] = pages.splice(from, 1);
    pages.splice(to, 0, page);
    pageSel = to;
    pagesChanged();
  };
  const moveWidget = (from, to) => {
    const list = config.pages[pageSel].widgets;
    if (to < 0 || to >= list.length || to === from) return;
    const [w] = list.splice(from, 1);
    list.splice(to, 0, w);
    pagesChanged();
  };
  sortable(pagesEl, ".ct-pill[data-i]", null, movePage);
  sortable(widgetsEl, ".ct-wrow", ".ct-grip", moveWidget);
  // Alt + arrow keys move the focused pill or row, for keyboards.
  const arrow = (e) => (e.altKey ? { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key] || 0 : 0);
  pagesEl.addEventListener("keydown", (e) => {
    const pill = e.target.closest(".ct-pill[data-i]");
    const d = pill && arrow(e);
    if (!d) return;
    e.preventDefault();
    const to = Number(pill.dataset.i) + d;
    movePage(Number(pill.dataset.i), to);
    pagesEl.querySelector(`.ct-pill[data-i="${to}"]`)?.focus();
  });
  widgetsEl.addEventListener("keydown", (e) => {
    const grip = e.target.closest(".ct-grip");
    const d = grip && arrow(e);
    if (!d) return;
    e.preventDefault();
    const to = Number(grip.closest(".ct-wrow").dataset.idx) + d;
    moveWidget(to - d, to);
    widgetsEl.querySelector(`.ct-wrow[data-idx="${to}"] .ct-grip`)?.focus();
  });

  const addPage = () => {
    if (config.pages.length >= cat.max_pages) return;
    // A new page starts empty; the panel says so until a widget is added.
    config.pages.push({ name: `Page ${config.pages.length + 1}`, widgets: [] });
    pageSel = config.pages.length - 1;
    pagesChanged();
    pageName.focus();
    pageName.select();
  };
  const renderPages = () => {
    const pages = config.pages;
    pageSel = Math.min(pageSel, pages.length - 1);
    const page = pages[pageSel];

    pageCount.textContent = `${pages.length} of ${cat.max_pages}${pages.length > 1 ? " · drag to reorder" : ""}`;
    pagesEl.innerHTML = "";
    pages.forEach((p, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ct-pill";
      b.dataset.i = i;
      b.draggable = pages.length > 1;
      b.setAttribute("aria-pressed", String(i === pageSel));
      b.textContent = p.name;
      b.title = `Page ${i + 1}: ${p.name}`;
      b.addEventListener("click", () => selectPage(i));
      pagesEl.appendChild(b);
    });
    const plus = document.createElement("button");
    plus.type = "button";
    plus.className = "ct-pill ct-pill-add";
    plus.textContent = "+";
    plus.setAttribute("aria-label", "Add a page");
    plus.title = pages.length >= cat.max_pages ? `All ${cat.max_pages} pages are in use` : "Add a page";
    plus.disabled = pages.length >= cat.max_pages;
    plus.addEventListener("click", addPage);
    pagesEl.appendChild(plus);
    pageDel.hidden = pages.length < 2;

    renderHeaders();
    if (document.activeElement !== pageName) pageName.value = page.name;

    // On this page: the space bar, then a row per widget.
    sendHome();
    const spaces = used(page.widgets);
    const free = cat.page_capacity - spaces;
    spaceN.textContent = `${spaces} / ${cat.page_capacity} spaces`;
    spaceBar.innerHTML = "";
    page.widgets.forEach((w, i) => {
      for (let k = 0; k < sizeCost[w.size]; k++) spaceBar.insertAdjacentHTML("beforeend", `<i class="tint-${TINTS[i % TINTS.length]}"></i>`);
    });
    for (let k = spaces; k < cat.page_capacity; k++) spaceBar.insertAdjacentHTML("beforeend", "<i></i>");
    pageSpace.textContent = `A small widget takes 1 space, a half page 3, a full page all ${cat.page_capacity}. Widgets show in this order (a half page sits on the side where it comes first).`;
    if (openRow && !page.widgets.some((w) => w.id === openRow)) openRow = "";
    widgetsEl.innerHTML = "";
    page.widgets.forEach((w, idx) => {
      const known = widgetById[w.id];
      const label = known ? known.label : "Web page (deleted)";
      const sizes = cat.widget_sizes.filter((z) => !known || !known.sizes || known.sizes.includes(z.id));
      const room = free + sizeCost[w.size];
      const box = boxFor(w.id);
      const open = openRow === w.id;
      const li = document.createElement("li");
      li.className = `ct-wrow${open ? " is-open" : ""}`;
      li.dataset.idx = idx;
      li.innerHTML = `
        <button type="button" class="ct-grip" aria-label="Move ${esc(label)}: drag, or Alt + arrow keys" title="Drag to reorder">⠿</button>
        <span class="ct-wdot tint-${TINTS[idx % TINTS.length]}" aria-hidden="true"></span>
        <span class="ct-wname"><strong>${esc(label)}</strong><small>${esc(summary(w.id))}</small></span>
        <span class="ct-sizes" role="group" aria-label="Size of ${esc(label)}">${sizes
          .map((z) => `<button type="button" data-size="${z.id}" aria-pressed="${w.size === z.id}" ${z.id !== w.size && z.cost > room ? "disabled" : ""}>${esc(z.label.replace(/ page$/, ""))}</button>`)
          .join("")}</span>
        ${box ? `<button type="button" class="btn btn-ghost ct-wbtn" data-gear aria-expanded="${open}" aria-label="${open ? "Close" : "Open"} ${esc(label)} settings" title="${open ? "Done" : "Settings"}">${open ? doneIcon : gearIcon}</button>` : ""}
        <button type="button" class="btn btn-ghost ct-wbtn" data-remove aria-label="Take ${esc(label)} off this page" title="Take off this page">✕</button>`;
      li.querySelectorAll("[data-size]").forEach((b) =>
        b.addEventListener("click", () => {
          if (w.size === b.dataset.size) return;
          w.size = b.dataset.size;
          pagesChanged();
        }),
      );
      li.querySelector("[data-remove]").addEventListener("click", () => {
        page.widgets.splice(idx, 1);
        pagesChanged();
      });
      li.querySelector("[data-gear]")?.addEventListener("click", () => {
        openRow = open ? "" : w.id;
        if (openRow && box === "music") loadMusic();
        renderPages();
      });
      if (open) {
        const body = document.createElement("div");
        body.className = "ct-wbody";
        body.appendChild(boxes[box]);
        li.appendChild(body);
        homes[box].querySelector("[data-ct-away]").hidden = false;
      }
      widgetsEl.appendChild(li);
    });

    // Add widget: the ones not on this page (greyed when they don't fit), and a new web page.
    const onIds = page.widgets.map((w) => w.id);
    let fits = 0;
    const options = cat.widgets
      .filter((w) => !onIds.includes(w.id))
      .map((w) => {
        const sizes = cat.widget_sizes.filter((z) => !w.sizes || w.sizes.includes(z.id));
        const start = sizes.some((z) => z.id === "small") ? "small" : sizes[0].id;
        const ok = sizeCost[start] <= free;
        fits += ok ? 1 : 0;
        return `<option value="${esc(w.id)}" ${ok ? "" : "disabled"}>${esc(w.label)}${ok ? "" : " (no room)"}</option>`;
      });
    addWidget.innerHTML = `<option value="">+ Add widget</option>${options.join("")}<option value="__web">Web page…</option>`;
    addFull.hidden = fits > 0 || !options.length;

    const every = config.rotation.every_s;
    rotateOn.checked = every > 0;
    rotateEvery.disabled = every === 0;
    if (document.activeElement !== rotateEvery) rotateEvery.value = every || lastEvery;
    refreshSummaries();
  };
  // Row summaries and the turning line follow edits made elsewhere (Look, Idle, an open row).
  const refreshSummaries = () => {
    widgetsEl.querySelectorAll(".ct-wrow").forEach((li) => {
      const w = config.pages[pageSel]?.widgets[li.dataset.idx];
      if (w) li.querySelector(".ct-wname small").textContent = summary(w.id);
    });
    const every = config.rotation.every_s;
    turnText.textContent = every
      ? `Pages turn every ${every} s, resume after ${config.rotation.resume_after_s} s untouched`
      : "Pages don't turn by themselves";
  };
  addWidget.addEventListener("change", () => {
    const id = addWidget.value;
    addWidget.value = "";
    if (!id) return;
    if (id === "__web") {
      webAddPage.hidden = false;
      webAddPage.querySelector("[data-ct-web-name]").focus();
      return;
    }
    const w = widgetById[id];
    const sizes = cat.widget_sizes.filter((z) => !w.sizes || w.sizes.includes(z.id));
    config.pages[pageSel].widgets.push({ id, size: sizes.some((z) => z.id === "small") ? "small" : sizes[0].id });
    pagesChanged();
  });
  webAddPage.querySelector("[data-ct-web-cancel]").addEventListener("click", () => (webAddPage.hidden = true));
  homes.music.addEventListener("toggle", () => {
    if (homes.music.open) loadMusic();
  });

  // ── page header: the config default (data-bind) and a page's own (data-ct-ph) ──
  // One summary line under the page name; Edit opens the fields.
  const phOwn = root.querySelector("[data-ct-ph-own]");
  const phFields = root.querySelector("[data-ct-ph-fields]");
  const phInputs = root.querySelectorAll("[data-ct-ph]");
  const hdrSummary = root.querySelector("[data-ct-hdr-summary]");
  const hdrBox = root.querySelector("[data-ct-hdr-box]");
  const hdrEdit = root.querySelector("[data-ct-hdr-edit]");
  const modeLabel = (id) => (cat.header_modes.find((m) => m.id === id) || { label: id }).label;
  const renderHeaders = () => {
    const page = config.pages[pageSel];
    const own = page.header || null;
    phOwn.value = own ? "own" : "";
    phFields.hidden = !own;
    const shown = own || config.header;
    phInputs.forEach((el) => {
      const value = shown[el.dataset.ctPh];
      if (el.type === "checkbox") el.checked = !!value;
      else el.value = String(value);
    });
    // Hide after only means something with Auto-hide.
    root.querySelector("[data-ct-header-default] [data-ct-hide-after]").disabled = config.header.mode !== "auto";
    phFields.querySelector("[data-ct-hide-after]").disabled = shown.mode !== "auto";
    const parts = [modeLabel(shown.mode) + (shown.mode === "auto" ? ` (${shown.hide_after_s} s)` : "")];
    if (shown.mode !== "off") {
      const parts2 = ["time", "title", "weather", "dots"].filter((k) => shown[k]);
      parts.push(parts2.length ? parts2.join(", ") : "empty");
    }
    hdrSummary.textContent = `${own ? "This page's own" : "Default"} · ${parts.join(" · ")}`;
  };
  hdrEdit.addEventListener("click", () => {
    hdrBox.hidden = !hdrBox.hidden;
    hdrEdit.textContent = hdrBox.hidden ? "Edit" : "Done";
    hdrEdit.setAttribute("aria-expanded", String(!hdrBox.hidden));
  });
  phOwn.addEventListener("change", () => {
    const page = config.pages[pageSel];
    if (phOwn.value === "own") page.header = { ...config.header };
    else delete page.header;
    renderHeaders();
    changed();
  });
  phInputs.forEach((el) => {
    el.addEventListener("change", () => {
      const page = config.pages[pageSel];
      if (!page.header) return;
      const key = el.dataset.ctPh;
      page.header[key] = el.type === "checkbox" ? el.checked : key === "hide_after_s" ? Number(el.value) : el.value;
      renderHeaders();
      changed();
    });
  });

  // ── "Default" next to a colour: back to StonePi's own (cat.color_defaults) ──
  root.querySelectorAll("[data-ct-reset]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      const path = btn.dataset.ctReset;
      const input = root.querySelector(`input[type="color"][data-bind="${path}"]`);
      const value = (cat.color_defaults || {})[path];
      if (!input || !value) return;
      input.value = value.toLowerCase();
      input.dispatchEvent(new Event("input", { bubbles: true })); // same path as picking it by hand
    });
  });

  pageName.addEventListener("input", () => {
    config.pages[pageSel].name = pageName.value.slice(0, 30);
    pagesChanged();
  });
  pageName.addEventListener("change", () => {
    if (!pageName.value.trim()) {
      config.pages[pageSel].name = pageSel ? `Page ${pageSel + 1}` : "Home";
      pageName.value = config.pages[pageSel].name;
      pagesChanged();
    }
  });
  pageDel.addEventListener("click", () => {
    const pages = config.pages;
    const page = pages[pageSel];
    if (pages.length < 2 || !window.confirm(`Remove the page “${page.name}”?`)) return;
    pages.splice(pageSel, 1);
    pageSel = Math.min(pageSel, pages.length - 1);
    pagesChanged();
  });

  // ── web pages (F14): add one where it's used, without leaving the editor ──
  // The page is saved in Notify at once (shared with TRMNL); putting it on a page or the
  // screensaver is part of this config, so it's saved with the config as usual.
  const saverPagePick = root.querySelector('select[data-bind="idle.saver.web.page_id"]');
  root.querySelectorAll("[data-ct-web-add]").forEach((box) => {
    const msg = box.querySelector("[data-ct-web-msg]");
    box.querySelector("[data-ct-web-create]").addEventListener("click", async () => {
      const name = box.querySelector("[data-ct-web-name]").value.trim();
      const url = box.querySelector("[data-ct-web-url]").value.trim();
      msg.textContent = "Adding…";
      try {
        const res = await fetch(new URL("../api/web-pages", window.location.href).href, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-StonePi-CSRF": csrf },
          body: JSON.stringify({ name, url }),
        });
        const body = await res.json();
        if (!res.ok || !body.ok) throw new Error(body.message || "Couldn't add the page.");
        const page = body.page;
        cat.web_pages.push(page);
        const widget = { id: body.widget, label: `Web: ${page.name}`, source: "web", sizes: ["full"] };
        cat.widgets.push(widget);
        widgetById[widget.id] = widget;
        const option = new Option(page.name, page.id);
        saverPagePick.appendChild(option);
        if (box.dataset.ctWebAdd === "saver") {
          config.idle.saver.style = "web";
          config.idle.saver.web.page_id = page.id;
          renderBinds();
          renderSaver();
          saverNext = true;
          msg.textContent = `Added ${page.name}; the screensaver shows it.`;
        } else if (!config.pages[pageSel].widgets.length) {
          config.pages[pageSel].widgets = [{ id: widget.id, size: "full" }];
          msg.textContent = `Added ${page.name} to this page.`;
        } else if (config.pages.length < cat.max_pages) {
          config.pages.push({ name: page.name.slice(0, 30), widgets: [{ id: widget.id, size: "full" }] });
          pageSel = config.pages.length - 1;
          msg.textContent = `Added ${page.name} on a new page.`;
        } else {
          msg.textContent = `Added ${page.name}. All ${cat.max_pages} pages are in use: free one, then add it.`;
        }
        box.querySelector("[data-ct-web-name]").value = "";
        box.querySelector("[data-ct-web-url]").value = "";
        pagesChanged();
      } catch (err) {
        msg.textContent = err.message || "Couldn't add the page.";
      }
    });
  });

  // Turning pages: every_s 0 = off; the last interval comes back when switched on again.
  const rotateOn = root.querySelector("[data-ct-rotate-on]");
  const rotateEvery = root.querySelector("[data-ct-rotate-every]");
  let lastEvery = config.rotation.every_s || 20;
  rotateOn.addEventListener("change", () => {
    config.rotation.every_s = rotateOn.checked ? lastEvery : 0;
    renderPages();
    changed();
  });
  rotateEvery.addEventListener("change", () => {
    const value = Math.max(5, Math.min(3600, Math.round(Number(rotateEvery.value) || 20)));
    lastEvery = value;
    rotateEvery.value = value;
    if (config.rotation.every_s) config.rotation.every_s = value;
    changed();
  });
  const rotateTry = root.querySelector("[data-ct-rotate-try]");
  rotateTry.addEventListener("click", () => {
    const on = rotateTry.getAttribute("aria-pressed") !== "true";
    rotateTry.setAttribute("aria-pressed", String(on));
    send("rotate", { on });
  });
  frame.addEventListener("load", () => {
    // A reloaded preview starts with turning off; keep the button honest.
    rotateTry.setAttribute("aria-pressed", "false");
    if (musicNext) {
      musicNext = false;
      setTimeout(() => send("open", { action: "open:music" }), 300); // after the panel script starts
    }
  });

  // ── allowed actions + controls ───────────────────────────────────────
  const allowedBoxes = root.querySelectorAll("[data-ct-allowed]");
  const pageOf = (action) => (action && action.startsWith("page:") ? Number(action.slice(5)) : 0);
  const renderAllowed = () => {
    allowedBoxes.forEach((box) => (box.checked = config.allowed_actions.includes(box.dataset.ctAllowed)));
    root.querySelectorAll("[data-ct-action-label]").forEach((el) => {
      const n = pageOf(el.dataset.ctActionLabel);
      if (!n) return;
      const page = config.pages[n - 1];
      el.textContent = page ? `Go to page ${n} (${page.name})` : `Go to page ${n}`;
      el.classList.toggle("muted", !page);
    });
  };
  allowedBoxes.forEach((box) =>
    box.addEventListener("change", () => {
      const keep = new Set(config.allowed_actions);
      if (box.checked) keep.add(box.dataset.ctAllowed);
      else keep.delete(box.dataset.ctAllowed);
      keep.add("home");
      keep.add("back");
      config.allowed_actions = cat.actions.map((a) => a.id).filter((id) => keep.has(id));
      for (const [input, action] of Object.entries(config.controls)) {
        if (action && !keep.has(action)) config.controls[input] = null;
      }
      renderControls();
      changed();
    }),
  );

  const shortLabel = (action) => {
    if (!action) return "—";
    if (action.startsWith("open:")) return miniById[action.slice(5)]?.label || action;
    const n = pageOf(action);
    if (n) return config.pages[n - 1] ? `Page ${n}: ${config.pages[n - 1].name}` : `Page ${n} (none)`;
    return actionsById[action]?.label || action;
  };
  const controlSelects = root.querySelectorAll("[data-ct-control]");
  const renderControls = () => {
    controlSelects.forEach((sel) => {
      const input = sel.dataset.ctControl;
      const groups = {};
      config.allowed_actions.forEach((id) => {
        const a = actionsById[id];
        // Only pages that exist (or the one already mapped, so it isn't silently dropped).
        if (pageOf(id) > config.pages.length && config.controls[input] !== id) return;
        (groups[a.group] = groups[a.group] || []).push(a);
      });
      sel.innerHTML =
        '<option value="">— Nothing —</option>' +
        Object.entries(groups)
          .map(
            ([g, list]) =>
              `<optgroup label="${g}">${list.map((a) => `<option value="${a.id}">${esc(pageOf(a.id) ? shortLabel(a.id) : a.label)}</option>`).join("")}</optgroup>`,
          )
          .join("");
      sel.value = config.controls[input] || "";
    });
    // Labels around the drawing (full text in the tooltip when it's cut short)
    root.querySelectorAll("[data-ct-label]").forEach((t) => {
      const input = t.dataset.ctLabel;
      t.textContent = shortLabel(config.controls[input]);
      const button = t.closest("[data-input]");
      const name = cat.inputs.find((i) => i.id === input)?.label || input;
      if (button) button.title = `${name}: ${t.textContent}`;
    });
  };
  controlSelects.forEach((sel) =>
    sel.addEventListener("change", () => {
      config.controls[sel.dataset.ctControl] = sel.value || null;
      renderControls();
      changed();
    }),
  );

  // ── the drawing: map (Controls tab) or try (other tabs) ──────────────
  // Buttons tab: Holds (long presses) start folded.
  const holdsBtn = root.querySelector("[data-ct-holds]");
  const setHolds = (open) => {
    holdsBtn.setAttribute("aria-expanded", String(open));
    root.querySelectorAll("tr.kind-long").forEach((r) => (r.hidden = !open));
  };
  holdsBtn.addEventListener("click", () => setHolds(holdsBtn.getAttribute("aria-expanded") !== "true"));
  const selectInputRow = (input) => {
    if (input.endsWith("_long")) setHolds(true);
    root.querySelectorAll("[data-ct-input-row]").forEach((r) => r.classList.toggle("is-picked", r.dataset.ctInputRow === input));
    const sel = root.querySelector(`[data-ct-control="${input}"]`);
    sel?.scrollIntoView({ block: "nearest" });
    sel?.focus();
  };
  root.querySelectorAll("[data-ct-input-row]").forEach((row) => {
    const hit = () => {
      const base = row.dataset.ctInputRow.replace(/_long$/, "");
      root.querySelectorAll("[data-input]").forEach((el) => el.classList.toggle("is-hot", el.dataset.input === base));
    };
    row.addEventListener("mouseenter", hit);
    row.addEventListener("focusin", hit);
    row.addEventListener("mouseleave", () => root.querySelectorAll("[data-input].is-hot").forEach((el) => el.classList.remove("is-hot")));
  });

  const LONG_MS = 600;
  root.querySelectorAll("[data-input]").forEach((el) => {
    const input = el.dataset.input;
    let timer = null;
    let fired = false;
    const press = () => {
      if (tab === "controls") return selectInputRow(input);
      // Turns and swipes have no long-press: send straight away.
      if (input === "dial_cw" || input === "dial_ccw" || input.startsWith("swipe_")) return send("input", { id: input });
      fired = false;
      timer = setTimeout(() => {
        fired = true;
        send("input", { id: `${input}_long` });
      }, LONG_MS);
    };
    const release = () => {
      if (!timer) return;
      clearTimeout(timer);
      timer = null;
      if (!fired) send("input", { id: input });
    };
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      press();
    });
    el.addEventListener("pointerup", release);
    el.addEventListener("pointerleave", release);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (tab === "controls") selectInputRow(input);
        else send("input", { id: input });
      }
    });
  });
  root.querySelector("[data-dial]").addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const d = Math.abs(e.deltaX) >= Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      if (!d) return;
      if (tab === "controls") return selectInputRow(d > 0 ? "dial_cw" : "dial_ccw");
      send("input", { id: d > 0 ? "dial_cw" : "dial_ccw" });
    },
    { passive: false },
  );
  root.querySelectorAll("[data-ct-send]").forEach((b) => b.addEventListener("click", () => send(b.dataset.ctSend)));
  const quietBtn = root.querySelector("[data-ct-quiet]");
  quietBtn.addEventListener("click", () => {
    const on = quietBtn.getAttribute("aria-pressed") !== "true";
    quietBtn.setAttribute("aria-pressed", String(on));
    send("saver");
    send("quiet", { on });
  });

  // ── background ───────────────────────────────────────────────────────
  const bgTypeBtns = root.querySelectorAll("[data-ct-bg-type]");
  const bgThumb = root.querySelector("[data-ct-bg-thumb]");
  const bgSolid = root.querySelector("[data-ct-bg-solid]");
  const bgStatus = root.querySelector("[data-ct-bg-status]");
  let lastImage = config.idle.background.type === "image" ? config.idle.background.value : "";
  const renderBackground = () => {
    const bg = config.idle.background;
    bgTypeBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.ctBgType === bg.type)));
    root.querySelectorAll("[data-ct-bg-pane]").forEach((p) => (p.hidden = p.dataset.ctBgPane !== bg.type));
    root.querySelectorAll("[data-ct-gradient]").forEach((s) => s.classList.toggle("is-on", bg.type === "gradient" && s.dataset.ctGradient === bg.value));
    if (bg.type === "solid") bgSolid.value = bg.value.toLowerCase();
    const img = bg.type === "image" ? bg.value : lastImage;
    bgThumb.hidden = !img;
    if (img) bgThumb.src = api(`asset/${img}`);
  };
  bgTypeBtns.forEach((b) =>
    b.addEventListener("click", () => {
      const type = b.dataset.ctBgType;
      if (type === "image" && !lastImage) {
        config.idle.background = { ...config.idle.background, type: "image", value: "" };
        renderBackground();
        bgStatus.textContent = "Upload an image to use it.";
        return; // nothing to show until an upload lands
      }
      config.idle.background =
        type === "gradient" ? { type, value: "midnight" } : type === "solid" ? { type, value: "#101114" } : { type, value: lastImage };
      renderBackground();
      changed();
    }),
  );
  root.querySelectorAll("[data-ct-gradient]").forEach((s) =>
    s.addEventListener("click", () => {
      config.idle.background = { type: "gradient", value: s.dataset.ctGradient };
      renderBackground();
      changed();
    }),
  );
  bgSolid.addEventListener("input", () => {
    config.idle.background = { type: "solid", value: bgSolid.value.toUpperCase() };
    changed();
  });
  root.querySelector("[data-ct-bg-upload]").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    bgStatus.textContent = "Uploading…";
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(api("background"), { method: "POST", headers: { "X-StonePi-CSRF": csrf }, body: form });
      const body = await res.json();
      if (!res.ok || !body.ok) throw new Error(body.message || "Upload failed.");
      lastImage = body.asset;
      config.idle.background = { type: "image", value: body.asset };
      bgStatus.textContent = `Ready (${Math.round(body.bytes / 1024)} KB).`;
      renderBackground();
      changed();
    } catch (err) {
      bgStatus.textContent = err.message || "Upload failed.";
    }
    e.target.value = "";
  });

  // ── clock colours ────────────────────────────────────────────────────
  const quietOn = root.querySelector("[data-ct-quiet-color-on]");
  const quietColor = root.querySelector("[data-ct-quiet-color]");
  const quietWrap = root.querySelector("[data-ct-quiet-color-wrap]");
  const renderSwatches = () => {
    root.querySelectorAll("[data-ct-color]").forEach((s) => s.classList.toggle("is-on", s.dataset.ctColor.toUpperCase() === config.clock.color));
    quietOn.checked = !!config.clock.quiet_color;
    quietWrap.hidden = !config.clock.quiet_color;
    quietColor.value = (config.clock.quiet_color || "#7A1A1A").toLowerCase();
  };
  root.querySelectorAll("[data-ct-color]").forEach((s) =>
    s.addEventListener("click", () => {
      config.clock.color = s.dataset.ctColor.toUpperCase();
      renderBinds();
      renderSwatches();
      widgetClockChanged();
      changed();
    }),
  );
  quietOn.addEventListener("change", () => {
    config.clock.quiet_color = quietOn.checked ? quietColor.value.toUpperCase() : null;
    renderSwatches();
    changed();
  });
  quietColor.addEventListener("input", () => {
    config.clock.quiet_color = quietColor.value.toUpperCase();
    changed();
  });

  // ── weather location ─────────────────────────────────────────────────
  const weatherNow = root.querySelector("[data-ct-weather-now]");
  const geoQ = root.querySelector("[data-ct-geo-q]");
  const geoResults = root.querySelector("[data-ct-geo-results]");
  const renderWeather = () => {
    const w = config.weather;
    weatherNow.textContent = w.lat != null ? `Showing weather for ${w.label || `${w.lat}, ${w.lon}`}.` : "No weather location set.";
  };
  const search = async () => {
    geoResults.innerHTML = "<li class=\"hint\">Searching…</li>";
    try {
      const res = await fetch(`${api("geocode")}?q=${encodeURIComponent(geoQ.value)}`);
      const body = await res.json();
      if (!res.ok || !body.ok) throw new Error(body.message || "Search failed.");
      geoResults.innerHTML = body.results.length ? "" : '<li class="hint">No matches.</li>';
      body.results.forEach((r) => {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-ghost";
        b.textContent = r.label;
        b.addEventListener("click", () => {
          config.weather = { lat: r.lat, lon: r.lon, label: r.label };
          geoResults.innerHTML = "";
          renderWeather();
          changed();
        });
        li.appendChild(b);
        geoResults.appendChild(li);
      });
    } catch (err) {
      geoResults.innerHTML = `<li class="hint">${err.message || "Search failed."}</li>`;
    }
  };
  root.querySelector("[data-ct-geo-go]").addEventListener("click", search);
  geoQ.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      search();
    }
  });
  root.querySelector("[data-ct-geo-clear]").addEventListener("click", () => {
    config.weather = { lat: null, lon: null, label: "" };
    renderWeather();
    changed();
  });

  // ── music (F7): presets, default speaker ─────────────────────────────
  // Speakers and playlists come from the Car Thing service (it holds the Spotify token).
  const musicStatus = root.querySelector("[data-ct-music-status]");
  const musicData = { devices: [], playlists: [], loaded: false };
  const devicePath = (key) => `music.${key}`;
  const renderMusic = () => {
    root.querySelectorAll("[data-ct-music-device]").forEach((sel) => {
      const current = get(devicePath(sel.dataset.ctMusicDevice)) || { id: "", name: "" };
      const rows = musicData.devices.slice();
      if (current.id && !rows.some((d) => d.id === current.id)) rows.push({ id: current.id, name: `${current.name || "Saved speaker"} (not available now)` });
      const none = sel.dataset.ctMusicDevice === "default_device" ? "None" : "Where music is playing";
      sel.innerHTML = `<option value="">${none}</option>` + rows.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
      sel.value = current.id || "";
    });
    root.querySelectorAll("[data-ct-music-pick]").forEach((sel) => {
      const uri = get(`music.presets.${sel.dataset.ctMusicPick}.uri`) || "";
      sel.innerHTML = '<option value="">Choose a playlist…</option>' +
        musicData.playlists.map((p) => `<option value="${esc(p.uri)}">${esc(p.name)}</option>`).join("");
      sel.value = musicData.playlists.some((p) => p.uri === uri) ? uri : "";
    });
  };
  root.querySelectorAll("[data-ct-music-device]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const row = musicData.devices.find((d) => d.id === sel.value);
      const current = get(devicePath(sel.dataset.ctMusicDevice)) || {};
      set(devicePath(sel.dataset.ctMusicDevice), sel.value ? { id: sel.value, name: row ? row.name : current.name || "" } : { id: "", name: "" });
      changed();
    });
  });
  root.querySelectorAll("[data-ct-music-pick]").forEach((sel) => {
    sel.addEventListener("change", () => {
      if (!sel.value) return;
      const n = sel.dataset.ctMusicPick;
      const row = musicData.playlists.find((p) => p.uri === sel.value);
      set(`music.presets.${n}.uri`, sel.value);
      if (row && !get(`music.presets.${n}.name`)) set(`music.presets.${n}.name`, row.name.slice(0, 40));
      renderBinds();
      changed();
    });
  });
  const loadMusic = async () => {
    if (musicData.loaded || !musicStatus) return;
    musicData.loaded = true;
    try {
      const res = await fetch(api("music/catalog"));
      const body = await res.json();
      musicData.devices = body.devices || [];
      musicData.playlists = body.playlists || [];
      if (!body.ok) musicStatus.textContent = body.message || "Speakers and playlists aren't available right now.";
      else musicStatus.textContent = `${musicData.devices.length} speaker${musicData.devices.length === 1 ? "" : "s"} and ${musicData.playlists.length} playlist${musicData.playlists.length === 1 ? "" : "s"} from Spotify. Asleep speakers may be missing: open Spotify on one to wake it, then reload.`;
    } catch {
      musicStatus.textContent = "Speakers and playlists aren't available right now.";
    }
    renderMusic();
  };
  root.querySelector("[data-ct-music-open]")?.addEventListener("click", () => send("open", { action: "open:music" }));

  // ── status pills: the panel, and Spotify turning red when the service reports the sign-in gone ──
  const pill = document.querySelector("[data-ct-pill-status]");
  const pillText = pill?.querySelector("[data-ct-pill-status-text]");
  const spotPill = document.querySelector("[data-ct-pill-spotify]");
  const pillDot = pill?.querySelector(".ct-dot");
  const shortStatus = (s) => {
    if (!s.enabled) return "Panel off";
    if (!s.service) return "Panel on · service not running";
    if (!s.adb) return "Panel on · ADB missing";
    if (!(s.devices || []).length) return "Panel on · not plugged in";
    if (s.connected) return "Panel on · connected";
    return "Panel on · loading the panel";
  };
  const pollStatus = async () => {
    if (!pill) return;
    try {
      const res = await fetch(api("status"));
      const s = await res.json();
      pillText.textContent = shortStatus(s);
      pillDot.className = `ct-dot ${s.enabled ? "on" : "off"}`;
      pill.classList.toggle("is-warn", !s.enabled || !s.connected);
      if ((s.music || {}).error_code === "invalid_grant" && spotPill) {
        spotPill.classList.remove("is-warn");
        spotPill.classList.add("is-bad");
        spotPill.querySelector("[data-ct-pill-spotify-text]").textContent = "· sign-in expired · Reconnect";
      }
    } catch {
      pillText.textContent = "status unavailable";
    }
  };
  pollStatus();
  setInterval(pollStatus, 10000);

  // ── go ───────────────────────────────────────────────────────────────
  const renderAll = () => {
    renderBinds();
    renderSaver();
    renderPages();
    renderAllowed();
    renderControls();
    renderBackground();
    renderSwatches();
    renderWeather();
    renderMusic();
  };
  renderAll();
  let startTab = HASH_TAB[window.location.hash.slice(1)];
  if (!startTab) {
    try {
      startTab = localStorage.getItem(TAB_KEY);
    } catch {
      startTab = "";
    }
  }
  setTab(startTab || "pages", false);
})();
