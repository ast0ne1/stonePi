const THEME_KEY = "stonepi-theme";
const LEGACY_THEME_KEYS = ["newscast-theme", "fileserve-theme", "eventtrakr-theme"];

function readCookie(name) {
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)")
  );
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookie(name, value) {
  document.cookie =
    name + "=" + encodeURIComponent(value) + "; Path=/; SameSite=Lax; Max-Age=31536000";
}

function persistPref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_err) {
    /* private mode */
  }
  writeCookie(key, value);
}

function readShared(shared, legacy, fallback) {
  let value = readCookie(shared) || localStorage.getItem(shared);
  if (!value) {
    for (const key of legacy) {
      value = readCookie(key) || localStorage.getItem(key);
      if (value) break;
    }
  }
  if (value) {
    persistPref(shared, value);
    return value;
  }
  return fallback;
}

function resolvedTheme(pref) {
  if (pref === "light" || pref === "dark") return pref;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}


function applyTheme(pref) {
  const theme = resolvedTheme(pref);
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePref = pref;
  document.documentElement.style.colorScheme = theme;
  const meta = document.querySelector("[data-theme-color]");
  var color = getComputedStyle(document.documentElement).getPropertyValue("--theme-meta").trim();
    if (meta && color) meta.setAttribute("content", color);
}

applyTheme(readShared(THEME_KEY, LEGACY_THEME_KEYS, "system"));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (readShared(THEME_KEY, LEGACY_THEME_KEYS, "system") === "system") applyTheme("system");
});

function askConfirm({ title, body, okLabel }) {
  const sheet = document.querySelector("[data-confirm-sheet]");
  if (!sheet) return Promise.resolve(window.confirm(title));
  return new Promise((resolve) => {
    const titleEl = sheet.querySelector("[data-confirm-title]");
    const bodyEl = sheet.querySelector("[data-confirm-body]");
    const okBtn = sheet.querySelector("[data-confirm-ok]");
    const cancelBtn = sheet.querySelector("[data-confirm-cancel]");
    if (titleEl) titleEl.textContent = title;
    if (bodyEl) {
      bodyEl.hidden = !body;
      bodyEl.textContent = body || "";
    }
    if (okBtn) okBtn.textContent = okLabel || "Continue";
    const finish = (value) => {
      sheet.hidden = true;
      sheet.classList.remove("is-open");
      if (!document.querySelector(".sheet.is-open")) {
        document.documentElement.classList.remove("sheet-open");
        document.body.classList.remove("sheet-open");
      }
      sheet.removeEventListener("click", onBackdrop);
      okBtn?.removeEventListener("click", onOk);
      cancelBtn?.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onBackdrop = (event) => {
      if (event.target === sheet) finish(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") finish(false);
    };
    sheet.addEventListener("click", onBackdrop);
    okBtn?.addEventListener("click", onOk);
    cancelBtn?.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKey);
    if (sheet.parentElement !== document.body) {
      document.body.appendChild(sheet);
    }
    sheet.hidden = false;
    sheet.classList.add("is-open");
    document.documentElement.classList.add("sheet-open");
    document.body.classList.add("sheet-open");
    okBtn?.focus();
  });
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirmPassed === "1") {
      delete form.dataset.confirmPassed;
      return;
    }
    event.preventDefault();
    askConfirm({
      title: form.dataset.confirm,
      body: form.dataset.confirmDetail || "",
      okLabel: form.dataset.confirmOk || "Continue",
    }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmPassed = "1";
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  });
});

document.querySelectorAll("[data-focus]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.querySelector(button.dataset.focus);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus();
  });
});

function openSheetEl(sheet) {
  if (!sheet) return;
  if (sheet.parentElement !== document.body) {
    document.body.appendChild(sheet);
  }
  sheet.hidden = false;
  sheet.classList.add("is-open");
  document.documentElement.classList.add("sheet-open");
  document.body.classList.add("sheet-open");
}

function closeSheetEl(sheet) {
  if (!sheet) return;
  sheet.hidden = true;
  sheet.classList.remove("is-open");
  if (!document.querySelector(".sheet.is-open")) {
    document.documentElement.classList.remove("sheet-open");
    document.body.classList.remove("sheet-open");
  }
}

document.querySelectorAll("[data-open-sheet]").forEach((btn) => {
  btn.addEventListener("click", (event) => {
    event.preventDefault(); // the floating + is a link (no-JS fallback)
    const name = btn.getAttribute("data-open-sheet") || "";
    const sheet = document.querySelector(`[data-sheet="${name}"]`);
    openSheetEl(sheet);
  });
});

document.querySelectorAll("[data-sheet]").forEach((sheet) => {
  sheet.querySelectorAll("[data-close-sheet]").forEach((closeBtn) => {
    closeBtn.addEventListener("click", () => closeSheetEl(sheet));
  });
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) closeSheetEl(sheet);
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll("[data-sheet].is-open").forEach((sheet) => closeSheetEl(sheet));
});

document.querySelectorAll("[data-card-expand]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const card = btn.closest("[data-expand-card]");
    if (!card) return;
    const on = !card.classList.contains("is-expanded");
    card.classList.toggle("is-expanded", on);
    btn.setAttribute("aria-expanded", on ? "true" : "false");
    const body = card.querySelector(".pin-card-body");
    if (body) body.hidden = !on;
  });
});

if (new URLSearchParams(window.location.search).get("new") === "1") {
  const target =
    document.querySelector("#notice-text") || document.querySelector("#reminder-text");
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus();
  }
}

/* Board (pinboard-canvas.md): tick reminders and checklist items in place, long-press a
   card for its options, paste a web address to add a link, and focus an add sheet's
   first field. Without JS the same forms post and the page reloads. */

function boardFetch(form, body = new FormData(form)) {
  return fetch(form.action, {
    method: "POST",
    body,
    headers: { "X-Requested-With": "fetch" },
    credentials: "same-origin",
  }).then((res) =>
    res
      .json()
      .catch(() => ({ ok: false }))
      .then((body) => (res.ok && body.ok ? body : Promise.reject(body)))
  );
}

document.querySelectorAll("form[data-inline-toggle]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    if (!button || button.disabled) return;
    const card = form.closest("[data-card]");
    const apply = (on) => {
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
      if (form.dataset.inlineToggle === "reminder") {
        const field = form.querySelector('input[name="done"]');
        if (field) field.value = on ? "0" : "1";
        card?.classList.toggle("is-done", on);
      } else if (card) {
        const rows = card.querySelectorAll(".check-row").length;
        const ticked = card.querySelectorAll(".check-row.is-on").length;
        const progress = card.querySelector("[data-progress]");
        if (progress) progress.textContent = `${ticked} of ${rows} done`;
        card.classList.toggle("is-done", rows > 0 && ticked === rows);
      }
    };
    const was = button.classList.contains("is-on");
    const sent = new FormData(form); // before apply() flips the reminder's done field
    apply(!was);
    button.disabled = true;
    boardFetch(form, sent)
      .then((body) => {
        if (typeof body.done === "boolean" && body.done === was) apply(body.done);
      })
      .catch((body) => {
        apply(was);
        if (body && body.message) window.alert(body.message);
      })
      .finally(() => {
        button.disabled = false;
      });
  });
});

document.querySelectorAll("[data-card]").forEach((card) => {
  let timer = null;
  let start = null;
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };
  card.addEventListener("pointerdown", (event) => {
    if (card.closest("[data-layer]")) return; // the canvas handles its own long press
    if (event.button !== 0 || event.target.closest("a, button, input, textarea, select")) return;
    start = [event.clientX, event.clientY];
    timer = setTimeout(() => {
      timer = null;
      card.dataset.longPressed = "1";
      openSheetEl(document.querySelector(`[data-sheet="card-${card.dataset.card}"]`));
    }, 500);
  });
  card.addEventListener("pointermove", (event) => {
    if (timer && start && Math.hypot(event.clientX - start[0], event.clientY - start[1]) > 8) cancel();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((type) => card.addEventListener(type, cancel));
  card.addEventListener("contextmenu", (event) => {
    if (card.dataset.longPressed) {
      event.preventDefault();
      delete card.dataset.longPressed;
    }
  });
});

document.querySelectorAll("[data-open-sheet]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const sheet = document.querySelector(`[data-sheet="${btn.getAttribute("data-open-sheet")}"]`);
    const field = sheet?.querySelector("[data-autofocus]");
    if (field) setTimeout(() => field.focus(), 60);
  });
});

const linkSheet = document.querySelector('[data-sheet="add-link"]');
if (linkSheet) {
  document.addEventListener("paste", (event) => {
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    if (document.querySelector(".sheet.is-open")) return;
    const text = (event.clipboardData?.getData("text") || "").trim();
    if (!text || /\s/.test(text) || !/^(https?:\/\/)?[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(text)) return;
    event.preventDefault();
    const field = linkSheet.querySelector("[data-link-url]");
    if (field) field.value = /^https?:\/\//i.test(text) ? text : `https://${text}`;
    openSheetEl(linkSheet);
  });
}

/* Canvas view (pinboard-canvas.md, sections 4 and 7): the same card elements as the list, placed
   freely. Drag the background (or two fingers) to pan, ctrl or cmd + wheel or a pinch to zoom,
   plain wheel to scroll around; drag a card or an area's title to move it, a corner to
   resize it; double-click empty space to add a card there. Moves are saved when you let
   go. While the board is open it checks every few seconds for other people's changes:
   moves are applied in place, anything else reloads the page when you're not busy. */
(() => {
  const canvasEl = document.querySelector("[data-canvas]");
  const dataEl = document.getElementById("board-canvas");
  const apiLink = document.querySelector("[data-board-api]");
  if (!canvasEl || !dataEl || !apiLink) return;
  const listEl = document.getElementById("board");
  const switcher = document.querySelector("[data-view-switch]");
  const viewport = canvasEl.querySelector("[data-viewport]");
  const layer = canvasEl.querySelector("[data-layer]");
  const zoomLabel = canvasEl.querySelector("[data-zoom-label]");
  // The page's links carry the app's path prefix (/pinboard on the Pi); static JS doesn't.
  const apiUrl = apiLink.getAttribute("href");
  const base = apiUrl.replace(/\/api\/cards$/, "");
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || "";
  const VIEW_KEY = "pinboard-view";
  const MIN_SCALE = 0.25;
  const MAX_SCALE = 2;
  const CARD_MIN = [140, 90];
  const AREA_MIN = [260, 160];
  const NEW_CARD = [220, 160];

  let state = JSON.parse(dataEl.textContent || "{}");
  state = { cards: state.cards || [], areas: state.areas || [] };
  let view = { tx: 24, ty: 24, scale: 1 };
  let mode = "list";
  let builtOnce = false;
  let busy = false;
  let suppressClick = false;
  const cardEls = new Map();
  const areaEls = new Map();
  const homes = new Map();

  document.querySelectorAll(".bcard[data-card]").forEach((el) => {
    cardEls.set(el.dataset.card, el);
    homes.set(el, [el.parentElement, el.nextElementSibling]);
  });

  const readPrefs = () => {
    try {
      return JSON.parse(localStorage.getItem(VIEW_KEY) || "null");
    } catch (_err) {
      return null;
    }
  };
  const savePrefs = () => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify({ mode, ...view }));
    } catch (_err) {
      /* private mode: the view just isn't remembered */
    }
  };

  const boxOf = (el) => ({
    x: parseFloat(el.dataset.x) || 0,
    y: parseFloat(el.dataset.y) || 0,
    w: parseFloat(el.dataset.w) || NEW_CARD[0],
    h: parseFloat(el.dataset.h) || NEW_CARD[1],
  });
  function setBox(el, box) {
    el.dataset.x = box.x;
    el.dataset.y = box.y;
    el.dataset.w = box.w;
    el.dataset.h = box.h;
    if (mode !== "canvas") return;
    el.style.left = `${box.x}px`;
    el.style.top = `${box.y}px`;
    el.style.width = `${box.w}px`;
    el.style.height = `${box.h}px`;
  }

  function applyView() {
    layer.style.transform = `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`;
    if (zoomLabel) zoomLabel.textContent = `${Math.round(view.scale * 100)}%`;
  }
  function zoomAt(factor, cx, cy) {
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
    const k = next / view.scale;
    view.tx = cx - (cx - view.tx) * k;
    view.ty = cy - (cy - view.ty) * k;
    view.scale = next;
    applyView();
  }
  function toBoard(clientX, clientY) {
    const r = viewport.getBoundingClientRect();
    return [(clientX - r.left - view.tx) / view.scale, (clientY - r.top - view.ty) / view.scale];
  }
  function fit() {
    const boxes = [...cardEls.values()].map(boxOf).concat([...areaEls.values()].map(boxOf));
    if (!boxes.length) return;
    const left = Math.min(...boxes.map((b) => b.x));
    const top = Math.min(...boxes.map((b) => b.y));
    const right = Math.max(...boxes.map((b) => b.x + b.w));
    const bottom = Math.max(...boxes.map((b) => b.y + b.h));
    const pad = 32;
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    view.scale = Math.min(1, Math.max(MIN_SCALE, Math.min((vw - pad * 2) / (right - left), (vh - pad * 2) / (bottom - top))));
    view.tx = pad - left * view.scale + Math.max(0, (vw - pad * 2 - (right - left) * view.scale) / 2);
    view.ty = pad - top * view.scale;
    applyView();
    savePrefs();
  }

  function buildArea(area) {
    let el = areaEls.get(area.id);
    if (!el) {
      el = document.createElement("div");
      el.className = "cv-area";
      el.dataset.area = area.id;
      el.innerHTML = '<div class="cv-area-head"><span></span></div><span class="cv-resize" aria-hidden="true"></span>';
      areaEls.set(area.id, el);
    }
    el.querySelector(".cv-area-head span").textContent = area.name;
    setBox(el, area);
    if (!el.parentElement) layer.prepend(el);
    return el;
  }

  function sizeViewport() {
    if (mode !== "canvas") return;
    const top = viewport.getBoundingClientRect().top;
    // Fill down to the bottom bar on a phone; on a wide screen the nav is a sidebar.
    const nav = document.querySelector(".nav")?.getBoundingClientRect();
    const bottom = nav && nav.top > top ? nav.top : window.innerHeight;
    const help = canvasEl.querySelector(".canvas-help");
    const below = help && getComputedStyle(help).display !== "none" ? help.offsetHeight + 8 : 0;
    viewport.style.height = `${Math.max(320, bottom - top - below - 16)}px`;
  }

  function enterCanvas() {
    mode = "canvas";
    document.body.classList.add("is-canvas");
    listEl.hidden = true;
    canvasEl.hidden = false;
    state.areas.forEach(buildArea);
    cardEls.forEach((el) => {
      if (!el.querySelector(":scope > .cv-resize")) {
        const handle = document.createElement("span");
        handle.className = "cv-resize";
        handle.setAttribute("aria-hidden", "true");
        el.appendChild(handle);
      }
      el.style.zIndex = String(10 + (parseInt(el.dataset.z, 10) || 0));
      layer.appendChild(el);
      setBox(el, boxOf(el));
    });
    sizeViewport();
    const prefs = readPrefs();
    if (!builtOnce && prefs && prefs.mode === "canvas" && prefs.scale) {
      view = { tx: prefs.tx, ty: prefs.ty, scale: prefs.scale };
      applyView();
    } else if (!builtOnce) {
      fit();
    }
    applyView();
    builtOnce = true;
    updateSwitch();
    savePrefs();
  }

  function enterList() {
    mode = "list";
    document.body.classList.remove("is-canvas");
    cardEls.forEach((el) => {
      const [parent, next] = homes.get(el) || [];
      el.removeAttribute("style");
      if (parent) parent.insertBefore(el, next && next.parentElement === parent ? next : null);
    });
    canvasEl.hidden = true;
    listEl.hidden = false;
    updateSwitch();
    savePrefs();
  }

  function updateSwitch() {
    switcher?.querySelectorAll("[data-view]").forEach((btn) => {
      const on = btn.dataset.view === mode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function post(path, fields) {
    const body = new FormData();
    body.append("csrf_token", csrf);
    Object.entries(fields).forEach(([k, v]) => body.append(k, String(Math.round(v * 10) / 10)));
    return fetch(`${base}${path}`, {
      method: "POST",
      body,
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    }).then((res) =>
      res
        .json()
        .catch(() => ({ ok: false }))
        .then((data) => (res.ok && data.ok ? data : Promise.reject(data)))
    );
  }

  // --- gestures ----------------------------------------------------------------------
  const pointers = new Map();
  let gesture = null;

  function startPinch() {
    const [a, b] = [...pointers.values()];
    const r = viewport.getBoundingClientRect();
    gesture = {
      kind: "pinch",
      dist: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1,
      mid: [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top],
      view0: { ...view },
      moved: true,
    };
    busy = true;
  }

  viewport.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const target = event.target;
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    if (pointers.size === 2) {
      if (gesture && gesture.el) setBox(gesture.el, gesture.box);
      startPinch();
      return;
    }
    if (pointers.size > 2) return;
    const resize = target.closest(".cv-resize");
    const start = [event.clientX, event.clientY];
    if (resize) {
      const el = resize.closest(".bcard, .cv-area");
      gesture = { kind: el.classList.contains("cv-area") ? "resize-area" : "resize-card", el, start, box: boxOf(el) };
    } else if (target.closest("a, button, input, textarea, select, label")) {
      pointers.delete(event.pointerId);
      return; // a link, tick or menu inside a card: let it click
    } else if (target.closest(".bcard")) {
      const el = target.closest(".bcard");
      const g = { kind: "card", el, start, box: boxOf(el) };
      // Holding still on a card opens its options, as in the list.
      g.timer = setTimeout(() => {
        if (gesture === g && !g.moved) {
          gesture = null;
          pointers.clear();
          openSheetEl(document.querySelector(`[data-sheet="card-${el.dataset.card}"]`));
        }
      }, 500);
      gesture = g;
    } else if (target.closest(".cv-area-head")) {
      const el = target.closest(".cv-area");
      const members = [...cardEls.values()].filter((c) => c.dataset.area === el.dataset.area);
      gesture = { kind: "area", el, start, box: boxOf(el), members: members.map((c) => [c, boxOf(c)]) };
    } else {
      gesture = { kind: "pan", start, view0: { ...view } };
    }
    gesture.moved = false;
    try {
      viewport.setPointerCapture(event.pointerId);
    } catch (_err) {
      /* no live pointer behind the event; the moves still reach the viewport */
    }
  });

  viewport.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, [event.clientX, event.clientY]);
    if (!gesture) return;
    if (gesture.kind === "pinch") {
      if (pointers.size < 2) return;
      const [a, b] = [...pointers.values()];
      const r = viewport.getBoundingClientRect();
      const mid = [(a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top];
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, gesture.view0.scale * (Math.hypot(a[0] - b[0], a[1] - b[1]) / gesture.dist)));
      const k = scale / gesture.view0.scale;
      view.scale = scale;
      view.tx = mid[0] - (gesture.mid[0] - gesture.view0.tx) * k;
      view.ty = mid[1] - (gesture.mid[1] - gesture.view0.ty) * k;
      applyView();
      return;
    }
    const dx = event.clientX - gesture.start[0];
    const dy = event.clientY - gesture.start[1];
    if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
    clearTimeout(gesture.timer);
    if (document.querySelector(".sheet.is-open")) {
      gesture = null; // a long press opened the card's options
      return;
    }
    gesture.moved = true;
    busy = true;
    const bx = dx / view.scale;
    const by = dy / view.scale;
    const b = gesture.box;
    if (gesture.kind === "pan") {
      view.tx = gesture.view0.tx + dx;
      view.ty = gesture.view0.ty + dy;
      applyView();
    } else if (gesture.kind === "card") {
      gesture.el.classList.add("is-dragging");
      setBox(gesture.el, { ...b, x: b.x + bx, y: b.y + by });
    } else if (gesture.kind === "resize-card") {
      setBox(gesture.el, { ...b, w: Math.max(CARD_MIN[0], b.w + bx), h: Math.max(CARD_MIN[1], b.h + by) });
    } else if (gesture.kind === "area") {
      setBox(gesture.el, { ...b, x: b.x + bx, y: b.y + by });
      gesture.members.forEach(([el, cb]) => setBox(el, { ...cb, x: cb.x + bx, y: cb.y + by }));
    } else if (gesture.kind === "resize-area") {
      setBox(gesture.el, { ...b, w: Math.max(AREA_MIN[0], b.w + bx), h: Math.max(AREA_MIN[1], b.h + by) });
    }
  });

  function endPointer(event) {
    if (!pointers.has(event.pointerId)) return;
    pointers.delete(event.pointerId);
    const g = gesture;
    if (!g) return;
    clearTimeout(g.timer);
    if (g.kind === "pinch") {
      if (pointers.size === 0) {
        gesture = null;
        busy = false;
        savePrefs();
      }
      return;
    }
    gesture = null;
    busy = false;
    if (!g.moved) return;
    suppressClick = true;
    setTimeout(() => {
      suppressClick = false;
    }, 0);
    if (g.kind === "pan") {
      savePrefs();
    } else if (g.kind === "card" || g.kind === "resize-card") {
      g.el.classList.remove("is-dragging");
      const box = boxOf(g.el);
      post(`/cards/${g.el.dataset.card}/place`, box)
        .then((res) => {
          g.el.dataset.area = res.area_id || "";
          g.el.dataset.z = res.z;
          g.el.style.zIndex = String(10 + res.z);
          setBox(g.el, { x: res.x, y: res.y, w: res.w, h: res.h });
        })
        .catch((res) => {
          setBox(g.el, g.box);
          if (res && res.message) window.alert(res.message);
        });
    } else {
      post(`/areas/${g.el.dataset.area}/place`, boxOf(g.el)).catch((res) => {
        setBox(g.el, g.box);
        (g.members || []).forEach(([el, cb]) => setBox(el, cb));
        if (res && res.message) window.alert(res.message);
      });
    }
  }
  viewport.addEventListener("pointerup", endPointer);
  viewport.addEventListener("pointercancel", endPointer);
  viewport.addEventListener(
    "click",
    (event) => {
      if (suppressClick) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const r = viewport.getBoundingClientRect();
      if (event.ctrlKey || event.metaKey) {
        zoomAt(Math.exp(-event.deltaY * 0.01), event.clientX - r.left, event.clientY - r.top);
      } else {
        view.tx -= event.shiftKey ? event.deltaY : event.deltaX;
        view.ty -= event.shiftKey ? 0 : event.deltaY;
        applyView();
      }
      clearTimeout(viewport._saveTimer);
      viewport._saveTimer = setTimeout(savePrefs, 300);
    },
    { passive: false }
  );

  // --- adding on the canvas ---------------------------------------------------------
  function placeAddForms(point) {
    document.querySelectorAll("[data-place-x]").forEach((input) => {
      input.value = point ? String(Math.round(point[0] - NEW_CARD[0] / 2)) : "";
    });
    document.querySelectorAll("[data-place-y]").forEach((input) => {
      input.value = point ? String(Math.round(point[1] - NEW_CARD[1] / 2)) : "";
    });
    document.querySelectorAll("[data-area-field]").forEach((field) => {
      field.hidden = Boolean(point);
    });
  }
  function viewportCentre() {
    const r = viewport.getBoundingClientRect();
    return toBoard(r.left + r.width / 2, r.top + r.height / 2);
  }
  // The + button: in the canvas a new card goes in the middle of what you're looking at.
  document.querySelectorAll('[data-open-sheet="add-pick"]').forEach((btn) => {
    btn.addEventListener("click", () => placeAddForms(mode === "canvas" ? viewportCentre() : null), true);
  });
  viewport.addEventListener("dblclick", (event) => {
    if (event.target.closest(".bcard, .cv-area-head, .cv-resize")) return;
    placeAddForms(toBoard(event.clientX, event.clientY));
    openSheetEl(document.querySelector('[data-sheet="add-pick"]'));
  });

  // --- toolbar and switch -----------------------------------------------------------
  canvasEl.querySelectorAll("[data-zoom]").forEach((btn) => {
    btn.addEventListener("click", () => {
      zoomAt(btn.dataset.zoom === "in" ? 1.25 : 0.8, viewport.clientWidth / 2, viewport.clientHeight / 2);
      savePrefs();
    });
  });
  canvasEl.querySelector("[data-fit]")?.addEventListener("click", fit);
  if (switcher) {
    switcher.hidden = false;
    switcher.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => (btn.dataset.view === "canvas" ? enterCanvas() : enterList()));
    });
  }
  window.addEventListener("resize", sizeViewport);

  // --- other people's changes -------------------------------------------------------
  function sameIds(a, b) {
    return a.length === b.length && a.every((id) => b.includes(id));
  }
  function poll() {
    if (document.hidden || busy || document.querySelector(".sheet.is-open")) return;
    fetch(apiUrl, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((next) => {
        if (busy || document.querySelector(".sheet.is-open")) return;
        const changed =
          !sameIds([...cardEls.keys()], next.cards.map((c) => c.id)) ||
          next.cards.some((c) => cardEls.get(c.id)?.dataset.rev !== c.rev) ||
          !sameIds(state.areas.map((a) => `${a.id}:${a.name}`), next.areas.map((a) => `${a.id}:${a.name}`));
        if (changed) {
          savePrefs();
          window.location.reload();
          return;
        }
        next.cards.forEach((c) => {
          const el = cardEls.get(c.id);
          if (!el) return;
          el.dataset.area = c.area_id;
          el.dataset.z = c.z;
          if (mode === "canvas") el.style.zIndex = String(10 + c.z);
          setBox(el, c);
        });
        next.areas.forEach((a) => (mode === "canvas" ? buildArea(a) : null));
        state = next;
      })
      .catch(() => {
        /* offline or signed out: try again next time */
      });
  }
  setInterval(poll, 5000);

  // Canvas by default on a wide screen, or whichever view this person used last.
  const prefs = readPrefs();
  const wide = window.matchMedia("(min-width: 900px)").matches;
  if ((prefs && prefs.mode === "canvas") || (!prefs && wide)) enterCanvas();
  else updateSwitch();
  if (location.hash.startsWith("#card-") && mode === "canvas") {
    const el = document.getElementById(location.hash.slice(1));
    if (el) {
      const b = boxOf(el);
      view.tx = viewport.clientWidth / 2 - (b.x + b.w / 2) * view.scale;
      view.ty = viewport.clientHeight / 2 - (b.y + b.h / 2) * view.scale;
      applyView();
    }
  }
})();

/* Adding links from elsewhere (pinboard-canvas.md, section 5). /add?url=... opens the Board
   with Add link filled in; Settings -> Board builds the bookmarklet and the Shortcut's
   address from this page's own address, so they carry the right host and prefix. */
document.querySelectorAll("[data-sheet][data-open-on-load]").forEach((sheet) => {
  openSheetEl(sheet);
  sheet.querySelector("button[type=submit]")?.focus();
  // Back on the board's own address, so a reload doesn't open the form again.
  history.replaceState(null, "", location.pathname.replace(/add\/?$/, ""));
});

const addLink = document.querySelector("[data-add-url]");
if (addLink) {
  const addUrl = new URL(addLink.getAttribute("href"), location.href).href;
  const bookmarklet = document.querySelector("[data-bookmarklet]");
  if (bookmarklet) {
    bookmarklet.href =
      "javascript:(()=>{window.open(" +
      JSON.stringify(`${addUrl}?url=`) +
      "+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title))})()";
    bookmarklet.addEventListener("click", (event) => {
      event.preventDefault();
      window.alert("Drag this button to your bookmarks bar, then click it on any page.");
    });
  }
  document.querySelectorAll("[data-shortcut-url]").forEach((el) => {
    el.textContent = `${addUrl}?url=`;
  });
  document.querySelectorAll("[data-copy-shortcut]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = `${addUrl}?url=`;
      const done = () => {
        btn.textContent = "Copied";
        setTimeout(() => (btn.textContent = "Copy"), 1500);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, () => window.prompt("Copy this:", text));
      } else {
        window.prompt("Copy this:", text);
      }
    });
  });
}
