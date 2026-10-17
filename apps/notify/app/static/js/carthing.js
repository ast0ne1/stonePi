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
  const changed = () => {
    const dirty = isDirty();
    dirtyEl.hidden = !dirty;
    dirtyEl.textContent = "Unsaved changes";
    resetBtn.disabled = !dirty;
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
  const tabs = root.querySelectorAll("[data-ct-tab]");
  const setTab = (name) => {
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
    let text = "";
    if (style === "matrix") {
      text = timeOn ? "" : "Matrix's Show time is off (Idle tab), so no clock shows on it.";
    } else if (style !== "clock") {
      text = timeOn
        ? `${styleName} shows a small time in the corner: it takes these colours, not the face. Choose Clock or Matrix under Idle → Screensaver style to see the face.`
        : `${styleName} shows no clock. Turn on Show time under Idle, or choose Clock or Matrix there, to see these.`;
    }
    note.textContent = text;
    note.hidden = !separate || !text;
    root.querySelector("[data-ct-face-hint]").textContent = separate
      ? "For the Clock and Clock & weather widgets."
      : "For the Clock and Clock & weather widgets and the Clock screensaver.";
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
      } else if (el.type === "number" || el.type === "range") value = Number(el.value);
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
      if (el.dataset.bind.startsWith("idle.saver.") || el.dataset.bind.startsWith("clock.saver.")) {
        renderSaver();
        saverNext = true; // show the screensaver once the preview reloads with the draft
      }
      changed();
    });
  });

  // ── pages ────────────────────────────────────────────────────────────
  const pagesEl = root.querySelector("[data-ct-pages]");
  const pageName = root.querySelector("[data-ct-page-name]");
  const pageLegend = root.querySelector("[data-ct-page-legend]");
  const pageSpace = root.querySelector("[data-ct-page-space]");
  const widgetsEl = root.querySelector("[data-ct-page-widgets]");
  const addPageBtn = root.querySelector("[data-ct-page-add]");
  const pagerLabel = root.querySelector("[data-ct-pager-label]");
  const used = (widgets) => widgets.reduce((n, w) => n + sizeCost[w.size], 0);
  const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
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
  const renderPages = () => {
    const pages = config.pages;
    pageSel = Math.min(pageSel, pages.length - 1);
    pagesEl.innerHTML = "";
    pages.forEach((page, i) => {
      const li = document.createElement("li");
      li.className = i === pageSel ? "is-on" : "";
      const n = page.widgets.length;
      li.innerHTML = `
        <button type="button" class="ct-page-pick" aria-pressed="${i === pageSel}">
          <strong>${i + 1}. ${esc(page.name)}</strong>
          <span class="muted">${n ? `${n} widget${n === 1 ? "" : "s"}` : "empty"}</span>
        </button>
        <span class="ct-order-btns">
          <button type="button" class="btn btn-ghost" data-up ${i === 0 ? "disabled" : ""} aria-label="Move ${esc(page.name)} earlier">↑</button>
          <button type="button" class="btn btn-ghost" data-down ${i === pages.length - 1 ? "disabled" : ""} aria-label="Move ${esc(page.name)} later">↓</button>
          <button type="button" class="btn btn-ghost" data-remove ${pages.length < 2 ? "disabled" : ""} aria-label="Remove ${esc(page.name)}">✕</button>
        </span>`;
      li.querySelector(".ct-page-pick").addEventListener("click", () => selectPage(i));
      const move = (d) => {
        [pages[i], pages[i + d]] = [pages[i + d], pages[i]];
        pageSel = i + d;
        pagesChanged();
      };
      li.querySelector("[data-up]").addEventListener("click", () => move(-1));
      li.querySelector("[data-down]").addEventListener("click", () => move(1));
      li.querySelector("[data-remove]").addEventListener("click", () => {
        if (pages.length < 2 || !window.confirm(`Remove the page “${page.name}”?`)) return;
        pages.splice(i, 1);
        pageSel = Math.min(pageSel, pages.length - 1);
        pagesChanged();
      });
      pagesEl.appendChild(li);
    });
    addPageBtn.disabled = pages.length >= cat.max_pages;

    const page = pages[pageSel];
    pageLegend.textContent = `Page ${pageSel + 1}: ${page.name}`;
    if (document.activeElement !== pageName) pageName.value = page.name;
    const spaces = used(page.widgets);
    pageSpace.textContent = `${spaces} of ${cat.page_capacity} spaces used. Widgets show in this order (a half page sits on the side where it comes first).`;
    const on = page.widgets.map((w) => w.id);
    const order = [...on, ...cat.widgets.map((w) => w.id).filter((id) => !on.includes(id))];
    widgetsEl.innerHTML = "";
    order.forEach((id) => {
      const idx = on.indexOf(id);
      const current = idx >= 0 ? page.widgets[idx] : null;
      const free = cat.page_capacity - spaces + (current ? sizeCost[current.size] : 0);
      const label = widgetById[id].label;
      const li = document.createElement("li");
      li.innerHTML = `
        <label class="check"><input type="checkbox" ${current ? "checked" : ""} ${!current && free < 1 ? "disabled" : ""} />
        <span>${esc(label)}</span></label>
        <select aria-label="Size of ${esc(label)}" ${current ? "" : "disabled"}>
          ${cat.widget_sizes
            .map((z) => {
              const pick = current ? current.size === z.id : z.id === "small";
              return `<option value="${z.id}" ${pick ? "selected" : ""} ${current && z.cost > free ? "disabled" : ""}>${z.label}</option>`;
            })
            .join("")}
        </select>
        <span class="ct-order-btns">
          <button type="button" class="btn btn-ghost" data-up ${idx <= 0 ? "disabled" : ""} aria-label="Move ${esc(label)} up">↑</button>
          <button type="button" class="btn btn-ghost" data-down ${idx < 0 || idx === on.length - 1 ? "disabled" : ""} aria-label="Move ${esc(label)} down">↓</button>
        </span>`;
      li.querySelector("input").addEventListener("change", (e) => {
        page.widgets = e.target.checked ? [...page.widgets, { id, size: "small" }] : page.widgets.filter((w) => w.id !== id);
        pagesChanged();
      });
      li.querySelector("select").addEventListener("change", (e) => {
        current.size = e.target.value;
        pagesChanged();
      });
      const swap = (d) => {
        const list = [...page.widgets];
        [list[idx], list[idx + d]] = [list[idx + d], list[idx]];
        page.widgets = list;
        pagesChanged();
      };
      li.querySelector("[data-up]").addEventListener("click", () => swap(-1));
      li.querySelector("[data-down]").addEventListener("click", () => swap(1));
      widgetsEl.appendChild(li);
    });

    pagerLabel.textContent = `Page ${pageSel + 1} of ${pages.length}: ${page.name}`;
    root.querySelectorAll("[data-ct-pager-step]").forEach((b) => (b.disabled = pages.length < 2));

    const every = config.rotation.every_s;
    rotateOn.checked = every > 0;
    rotateEvery.disabled = every === 0;
    if (document.activeElement !== rotateEvery) rotateEvery.value = every || lastEvery;
  };
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
  addPageBtn.addEventListener("click", () => {
    if (config.pages.length >= cat.max_pages) return;
    config.pages.push({ name: `Page ${config.pages.length + 1}`, widgets: [{ id: "clock", size: "full" }] });
    pageSel = config.pages.length - 1;
    pagesChanged();
  });
  root.querySelectorAll("[data-ct-pager-step]").forEach((b) => b.addEventListener("click", () => selectPage(pageSel + Number(b.dataset.ctPagerStep))));

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
  const selectInputRow = (input) => {
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
  tabs.forEach((b) => b.addEventListener("click", () => { if (b.dataset.ctTab === "music") loadMusic(); }));
  root.querySelector("[data-ct-music-open]")?.addEventListener("click", () => send("open", { action: "open:music" }));

  // ── device status ────────────────────────────────────────────────────
  const statusEl = document.querySelector("[data-ct-status-detail]");
  const pollStatus = async () => {
    try {
      const res = await fetch(api("status"));
      const s = await res.json();
      let text;
      if (!s.service) text = s.enabled ? "the Car Thing service isn't running" : "service stopped";
      else if (!s.adb) text = "ADB isn't installed on this machine";
      else if (!(s.devices || []).length) text = "no Car Thing plugged in";
      else if (s.connected) text = "Car Thing connected and showing the panel";
      else if ((s.devices || []).some((d) => d.injected)) text = "Car Thing found, waiting for it to load the panel";
      else text = "Car Thing found";
      if (s.error) text += ` (${s.error})`;
      statusEl.textContent = text;
    } catch {
      statusEl.textContent = "status unavailable";
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
  setTab(window.location.hash === "#controls" ? "controls" : "pages");
})();
