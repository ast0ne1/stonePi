const THEME_KEY = "stonepi-theme";
const PALETTE_KEY = "stonepi-palette";
const LEGACY_THEME_KEYS = ["newscast-theme", "fileserve-theme", "eventtrakr-theme"];
const LEGACY_PALETTE_KEYS = ["newscast-palette", "fileserve-palette", "eventtrakr-palette"];
const PALETTES = ["default", "ocean", "forest", "slate"];
const THEME_COLORS = {
  default: { light: "#f3eee4", dark: "#12100d" },
  ocean: { light: "#e7eef5", dark: "#0c141c" },
  forest: { light: "#eef1e6", dark: "#10140d" },
  slate: { light: "#ececee", dark: "#121314" },
};

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

function savedPalette() {
  const value = readShared(PALETTE_KEY, LEGACY_PALETTE_KEYS, "default");
  return PALETTES.includes(value) ? value : "default";
}

function applyTheme(pref, palette) {
  const theme = resolvedTheme(pref);
  const chosen = palette || savedPalette();
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePref = pref;
  document.documentElement.dataset.palette = chosen;
  document.documentElement.style.colorScheme = theme;
  const meta = document.querySelector("[data-theme-color]");
  if (meta) meta.setAttribute("content", THEME_COLORS[chosen][theme]);
  document.querySelectorAll("[data-theme-set]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.themeSet === pref);
  });
  document.querySelectorAll("[data-palette-set]").forEach((button) => {
    const on = button.dataset.paletteSet === chosen;
    button.classList.toggle("is-active", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const status = document.querySelector("[data-palette-status]");
  if (status) {
    const label = chosen.charAt(0).toUpperCase() + chosen.slice(1);
    status.textContent = `Using ${label} — saved for this browser (no Save button).`;
  }
}

const savedTheme = readShared(THEME_KEY, LEGACY_THEME_KEYS, "system");
applyTheme(savedTheme);
document.querySelectorAll("[data-theme-set]").forEach((button) => {
  button.addEventListener("click", () => {
    const pref = button.dataset.themeSet || "system";
    persistPref(THEME_KEY, pref);
    applyTheme(pref, savedPalette());
  });
});
document.querySelectorAll("[data-palette-set]").forEach((button) => {
  button.addEventListener("click", () => {
    const palette = button.dataset.paletteSet || "default";
    persistPref(PALETTE_KEY, palette);
    const pref = readCookie(THEME_KEY) || localStorage.getItem(THEME_KEY) || "system";
    applyTheme(pref, palette);
  });
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (readShared(THEME_KEY, LEGACY_THEME_KEYS, "system") === "system") applyTheme("system");
});

const VIEW_KEYS = {
  home: "stonepi-view-home",
  health: "stonepi-view-health",
  "health-version": "stonepi-view-health-version",
  "health-url": "stonepi-view-health-url",
  services: "stonepi-view-services",
  "services-route": "stonepi-view-services-route",
  "services-port": "stonepi-view-services-port",
};
const VIEW_ALLOWED = {
  home: ["cards", "list", "icons"],
  health: ["full", "expandable", "compact"],
  "health-version": ["1", "0"],
  "health-url": ["1", "0"],
  services: ["full", "expandable", "compact"],
  "services-route": ["1", "0"],
  "services-port": ["1", "0"],
};
const VIEW_DEFAULTS = {
  home: "cards",
  health: "full",
  "health-version": "1",
  "health-url": "1",
  services: "full",
  "services-route": "1",
  "services-port": "1",
};
const VIEW_DATASET = {
  home: "viewHome",
  health: "viewHealth",
  "health-version": "viewHealthVersion",
  "health-url": "viewHealthUrl",
  services: "viewServices",
  "services-route": "viewServicesRoute",
  "services-port": "viewServicesPort",
};

function readViewPref(set) {
  const key = VIEW_KEYS[set];
  const allowed = VIEW_ALLOWED[set] || [];
  const fallback = VIEW_DEFAULTS[set];
  let value = readCookie(key) || localStorage.getItem(key) || fallback;
  if (!allowed.includes(value)) value = fallback;
  return value;
}

function applyViewPrefsToDocument() {
  Object.keys(VIEW_KEYS).forEach((set) => {
    const value = readViewPref(set);
    document.documentElement.dataset[VIEW_DATASET[set]] = value;
  });
  document.querySelectorAll("[data-expand-card].is-expanded").forEach((card) => {
    card.classList.remove("is-expanded");
    const btn = card.querySelector("[data-card-expand]");
    if (btn) btn.setAttribute("aria-expanded", "false");
  });
}

function syncViewFormFromPrefs() {
  document.querySelectorAll("[data-view-set]").forEach((el) => {
    const set = el.dataset.viewSet;
    if (!set || !VIEW_KEYS[set]) return;
    const current = readViewPref(set);
    if (el instanceof HTMLInputElement && el.type === "checkbox") {
      el.checked = current === "1";
      return;
    }
    if (el instanceof HTMLInputElement && el.type === "radio") {
      el.checked = el.dataset.viewValue === current || el.value === current;
    }
  });
  syncViewFormDraftState();
}

function draftViewValue(set) {
  const radios = document.querySelectorAll(`[data-view-set="${set}"]`);
  if (!radios.length) return readViewPref(set);
  const first = radios[0];
  if (first instanceof HTMLInputElement && first.type === "checkbox") {
    return first.checked ? "1" : "0";
  }
  for (const el of radios) {
    if (el instanceof HTMLInputElement && el.type === "radio" && el.checked) {
      return el.dataset.viewValue || el.value || VIEW_DEFAULTS[set];
    }
  }
  return readViewPref(set);
}

function syncViewFormDraftState() {
  const healthMode = draftViewValue("health");
  const servicesMode = draftViewValue("services");
  const healthDetail = healthMode === "full" || healthMode === "expandable";
  const servicesDetail = servicesMode === "full" || servicesMode === "expandable";
  document.querySelectorAll('[data-view-set="health-version"], [data-view-set="health-url"]').forEach((el) => {
    if (el instanceof HTMLInputElement) el.disabled = !healthDetail;
  });
  document.querySelectorAll('[data-view-set="services-route"], [data-view-set="services-port"]').forEach((el) => {
    if (el instanceof HTMLInputElement) el.disabled = !servicesDetail;
  });
}

function saveViewPrefsFromForm() {
  Object.keys(VIEW_KEYS).forEach((set) => {
    let value = draftViewValue(set);
    const allowed = VIEW_ALLOWED[set] || [];
    if (!allowed.includes(value)) value = VIEW_DEFAULTS[set];
    persistPref(VIEW_KEYS[set], value);
  });
  applyViewPrefsToDocument();
  syncViewFormFromPrefs();
  const status = document.querySelector("[data-view-status]");
  if (status) status.textContent = "View options saved for this browser.";
}

applyViewPrefsToDocument();
syncViewFormFromPrefs();
document.querySelectorAll("[data-view-set]").forEach((el) => {
  el.addEventListener("change", () => {
    syncViewFormDraftState();
    const status = document.querySelector("[data-view-status]");
    if (status) status.textContent = "Unsaved changes — click Save view options.";
  });
});
document.querySelector("[data-view-save]")?.addEventListener("click", () => {
  saveViewPrefsFromForm();
});

(function expandableCards() {
  document.querySelectorAll("[data-card-expand]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const card = btn.closest("[data-expand-card]");
      if (!card) return;
      const on = !card.classList.contains("is-expanded");
      card.classList.toggle("is-expanded", on);
      btn.setAttribute("aria-expanded", on ? "true" : "false");
      const labelEl = btn.querySelector("[data-expand-label]");
      if (labelEl) labelEl.textContent = on ? "Hide" : "Details";
      const name = card.querySelector("h3")?.textContent?.trim();
      const label = on ? "Hide details" : "Show details";
      btn.setAttribute("aria-label", name ? `${label} for ${name}` : label);
      btn.title = label;
    });
  });
})();

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
    if (okBtn) {
      const label = okBtn.querySelector("[data-confirm-ok-label]");
      if (label) label.textContent = okLabel || "Continue";
      else okBtn.textContent = okLabel || "Continue";
    }
    const finish = (value) => {
      sheet.hidden = true;
      sheet.classList.remove("is-open");
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
    sheet.hidden = false;
    sheet.classList.add("is-open");
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
    const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
    askConfirm({
      title: form.dataset.confirm,
      body: form.dataset.confirmDetail || "",
      okLabel: form.dataset.confirmOk || "Continue",
    }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmPassed = "1";
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitter || undefined);
      } else {
        form.submit();
      }
    });
  });
});

(function initTrmnlDesigner() {
  const dataEl = document.getElementById("trmnl-designer-data");
  const root = document.querySelector("[data-trmnl-designer]");
  if (!dataEl || !root) return;

  let data;
  try {
    data = JSON.parse(dataEl.value || dataEl.textContent || "{}");
  } catch {
    return;
  }

  const paletteEl = root.querySelector("[data-trmnl-palette]");
  const canvasEl = root.querySelector("[data-trmnl-canvas]");
  const layoutInput = document.querySelector("[data-trmnl-layout]");
  const markupEl = document.querySelector("[data-trmnl-markup]");
  const copyBtn = document.querySelector("[data-trmnl-copy]");
  if (!paletteEl || !canvasEl || !layoutInput) return;

  const catalog = Array.isArray(data.catalog) ? data.catalog : [];
  const titleBar = data.title_bar || "";
  const preview = data.preview || {};
  const byId = Object.fromEntries(catalog.map((item) => [item.id, item]));
  const snippetsByDevice = data.snippets_by_device || {};
  const defaultsByDesign = data.defaults_by_design || {};
  const markupByDeviceDesign = data.markup_by_device_design || {};
  const deviceSelect = document.querySelector("[data-trmnl-device]");
  const designSelect = document.querySelector("[data-trmnl-design]");
  const designBlurb = designSelect?.closest("label")?.nextElementSibling;
  let device = data.device || "og";
  let design = data.design || "household";
  let fixedDesign = Boolean(data.fixed_design);
  let snippets = { ...(snippetsByDevice[device] || data.snippets || {}) };
  let layout = {
    cols: 2,
    device,
    design,
    items: Array.isArray(data.layout?.items) ? data.layout.items.map((item) => ({
      id: item.id,
      span: item.span >= 2 ? 2 : 1,
    })) : [],
  };
  let dragId = null;
  let dragFrom = null;
  const productBadges = { newscast: "NC", eventtrakr: "ET", fileserve: "FS", pinboard: "PB", studio: "ST", pricescout: "PS", sportguide: "SG" };

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fixedMarkup() {
    return String(markupByDeviceDesign[device]?.[design] || data.markup || "");
  }

  function buildMarkup() {
    if (fixedDesign) {
      return fixedMarkup() || data.markup || "";
    }
    const parts = ['<div class="layout layout--col gap">'];
    const items = layout.items;
    let i = 0;
    while (i < items.length) {
      const item = items[i];
      const snip = snippets[item.id];
      if (!snip) {
        i += 1;
        continue;
      }
      if (item.span >= 2) {
        parts.push(snip);
        i += 1;
        continue;
      }
      const pair = [snip];
      if (i + 1 < items.length && items[i + 1].span < 2 && snippets[items[i + 1].id]) {
        pair.push(snippets[items[i + 1].id]);
        i += 2;
      } else {
        i += 1;
      }
      parts.push('  <div class="grid grid--cols-2 gap">');
      parts.push(...pair);
      parts.push("  </div>");
    }
    parts.push("</div>");
    parts.push(String(titleBar).trimEnd());
    return `${parts.join("\n")}\n`;
  }

  function syncFields() {
    layout.device = device;
    layout.design = design;
    layoutInput.value = JSON.stringify(layout);
    if (markupEl) markupEl.value = buildMarkup();
  }

  function setFixedUi(on) {
    fixedDesign = Boolean(on);
    root.toggleAttribute("data-fixed-design", fixedDesign);
    paletteEl.hidden = fixedDesign;
    canvasEl.classList.toggle("is-fixed", fixedDesign);
  }

  function applyDevice(next) {
    device = next || "og";
    snippets = { ...(snippetsByDevice[device] || snippets) };
    syncCanvasDevice();
    syncFields();
    render();
  }

  function applyDesign(next) {
    design = next || "household";
    const preset = (data.designs || []).find((item) => item.id === design);
    setFixedUi(Boolean(preset?.fixed));
    if (designBlurb && preset?.blurb) designBlurb.textContent = preset.blurb;
    if (defaultsByDesign[design]) {
      const def = defaultsByDesign[design];
      layout = {
        cols: 2,
        device,
        design,
        items: Array.isArray(def.items)
          ? def.items.map((item) => ({ id: item.id, span: item.span >= 2 ? 2 : 1 }))
          : [],
      };
    }
    syncFields();
    render();
  }

  deviceSelect?.addEventListener("change", () => applyDevice(deviceSelect.value));
  designSelect?.addEventListener("change", () => applyDesign(designSelect.value));
  setFixedUi(fixedDesign);

  function previewBody(id) {
    if (id === "system") {
      const status = preview.system_ok
        ? '<span class="trmnl-pill ok">Operational</span>'
        : '<span class="trmnl-pill">Attention</span>';
      return `
        <div class="trmnl-row"><strong>${escapeHtml(preview.hostname || "stonepi")}</strong>${status}</div>
        <div class="trmnl-metrics">
          <div class="trmnl-metric"><span class="trmnl-kicker">CPU</span><strong>${escapeHtml(preview.cpu_disp)}</strong></div>
          <div class="trmnl-metric"><span class="trmnl-kicker">RAM</span><strong>${escapeHtml(preview.mem_disp)}</strong></div>
          <div class="trmnl-metric"><span class="trmnl-kicker">Temp</span><strong>${escapeHtml(preview.temp_disp)}</strong></div>
          <div class="trmnl-metric"><span class="trmnl-kicker">Uptime</span><strong>${escapeHtml(preview.uptime)}</strong></div>
        </div>`;
    }
    if (id === "watch") {
      return `
        <div class="trmnl-row">
          <strong>${escapeHtml(preview.watch_level || "Attention")}</strong>
          <span class="trmnl-pill">${escapeHtml(preview.watch_summary || "—")}</span>
        </div>`;
    }
    if (id === "apps") {
      const apps = Array.isArray(preview.apps) ? preview.apps : [];
      const limit = device === "og" ? 4 : 8;
      const rows = apps.slice(0, limit).map((app) => `
        <div class="trmnl-row">
          <span>${escapeHtml(app.n)}</span>
          <span class="trmnl-pill${app.running ? " ok" : ""}">${app.running ? "Up" : "Down"}</span>
        </div>`).join("");
      return rows || '<span class="trmnl-kicker">No apps</span>';
    }
    if (id === "newscast") {
      return `
        <div class="trmnl-row">
          <strong>${escapeHtml(preview.nc_feeds)} feeds</strong>
          <span class="trmnl-pill">${escapeHtml(preview.nc_updated)}</span>
        </div>`;
    }
    if (id === "eventtrakr") {
      return `
        <span class="trmnl-kicker">Next</span>
        <strong>${escapeHtml(preview.et_next)}</strong>`;
    }
    if (id === "fileserve") {
      return `
        <div class="trmnl-row">
          <strong>${escapeHtml(preview.fs_pages ?? 0)} pages</strong>
          <span class="trmnl-pill${preview.fs_ok ? " ok" : ""}">${preview.fs_ok ? "Up" : "Down"}</span>
        </div>`;
    }
    if (id === "pinboard") {
      const lines = Array.isArray(preview.pinboard_lines) ? preview.pinboard_lines : [];
      const more = Number(preview.pinboard_more || 0);
      const limit = device === "og" ? 1 : 2;
      const rows = lines.slice(0, limit).map((line) => `<div class="trmnl-kicker">${escapeHtml(line)}</div>`).join("");
      return `${rows || '<span class="trmnl-kicker">No notices</span>'}${more ? `<span class="trmnl-pill">+${more}</span>` : ""}`;
    }
    if (id === "storage") {
      return `
        <div class="trmnl-row">
          <div><span class="trmnl-kicker">Disk</span><strong>${escapeHtml(preview.disk_disp)}</strong></div>
          <div><span class="trmnl-kicker">Backup</span><strong>${escapeHtml(preview.backup_when)}</strong>
            <span class="trmnl-pill${preview.backup_ok ? " ok" : ""}">${preview.backup_ok ? "OK" : "Check"}</span>
          </div>
        </div>`;
    }
    return "";
  }

  function placedIds() {
    return new Set(layout.items.map((item) => item.id));
  }

  function renderProductTile(id) {
    const short = {
      newscast: device === "og" ? "News" : "NewsCast",
      eventtrakr: device === "og" ? "Events" : "EventTrakr",
      fileserve: device === "og" ? "Files" : "FileServe",
      pinboard: device === "og" ? "Pins" : "Pinboard",
    };
    const label = short[id] || (byId[id] || { label: id }).label;
    const badge = productBadges[id] || "";
    return `
      <div class="trmnl-tile trmnl-product">
        <div class="trmnl-tile-head">
          <div class="trmnl-tile-title">
            ${badge ? `<span class="trmnl-badge">${escapeHtml(badge)}</span>` : ""}
            <strong>${escapeHtml(label)}</strong>
          </div>
        </div>
        <div class="trmnl-preview-body">${previewBody(id)}</div>
      </div>`;
  }

  function syncCanvasDevice() {
    const baseW = device === "v2" ? 1040 : 800;
    const baseH = device === "v2" ? 780 : 480;
    canvasEl.dataset.device = device;
    canvasEl.style.setProperty("--trmnl-w", String(baseW));
    canvasEl.style.setProperty("--trmnl-h", String(baseH));
    canvasEl.classList.toggle("is-og", device === "og");
    canvasEl.classList.toggle("is-v2", device === "v2");
    canvasEl.classList.add("is-stage");
    canvasEl.classList.toggle("is-fixed", fixedDesign);
    // Fit the canvas column. Fixed designs hide the palette — don't leave the
    // canvas stuck in the 160–220px palette track (≥900px designer grid).
    const palette = root.querySelector("[data-trmnl-palette]");
    const split =
      !fixedDesign &&
      Boolean(palette) &&
      !palette.hasAttribute("hidden") &&
      window.matchMedia("(min-width: 900px)").matches;
    const paletteW = split ? Math.ceil(palette.getBoundingClientRect().width) : 0;
    const availW = Math.max(240, (root.clientWidth || 720) - paletteW - (split ? 12 : 0) - 8);
    const scale = Math.min(1, availW / baseW);
    canvasEl.style.setProperty("--trmnl-scale", String(Number(scale.toFixed(3))));
  }

  function titleBarHtml() {
    return `
      <div class="trmnl-titlebar">
        <span class="trmnl-titlebar-brand">StonePi</span>
        <span class="trmnl-titlebar-instance">${escapeHtml(preview.updated_at || "")}</span>
      </div>`;
  }

  function tileChrome(item, { removable }) {
    const meta = byId[item.id] || { id: item.id, label: item.id };
    const badge = productBadges[item.id];
    const actions = removable
      ? `<button type="button" class="trmnl-tile-x" data-remove aria-label="Remove ${escapeHtml(meta.label)}">×</button>`
      : "";
    return `
      <div class="trmnl-tile-head">
        <div class="trmnl-tile-title">
          ${badge ? `<span class="trmnl-badge">${escapeHtml(badge)}</span>` : ""}
          <strong>${escapeHtml(meta.label)}</strong>
        </div>
        ${actions}
      </div>
      <div class="trmnl-preview-body">${previewBody(item.id)}</div>`;
  }

  function bindTileInteractions(tile, item, index) {
    tile.draggable = true;
    tile.addEventListener("dragstart", (event) => {
      dragId = item.id;
      dragFrom = "canvas";
      tile.classList.add("is-dragging");
      event.dataTransfer.setData("text/plain", item.id);
      event.dataTransfer.effectAllowed = "move";
    });
    tile.addEventListener("dragend", () => tile.classList.remove("is-dragging"));
    tile.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = dragFrom === "palette" ? "copy" : "move";
    });
    tile.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const id = dragId || event.dataTransfer.getData("text/plain");
      if (!id || !byId[id]) return;
      placeAt(id, index);
    });
    tile.querySelector("[data-remove]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      layout.items = layout.items.filter((entry) => entry.id !== item.id);
      render();
    });
  }

  function renderFreeform() {
    syncCanvasDevice();
    const used = placedIds();
    paletteEl.innerHTML = "";
    catalog.forEach((item) => {
      if (used.has(item.id)) return;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "trmnl-palette-item";
      chip.draggable = true;
      chip.dataset.blockId = item.id;
      chip.innerHTML = `<strong>${escapeHtml(item.label)}</strong><span class="muted">${escapeHtml(item.blurb || "")}</span>`;
      chip.addEventListener("dragstart", (event) => {
        dragId = item.id;
        dragFrom = "palette";
        event.dataTransfer.setData("text/plain", item.id);
        event.dataTransfer.effectAllowed = "copyMove";
      });
      chip.addEventListener("click", () => {
        layout.items.push({ id: item.id, span: 1 });
        render();
      });
      paletteEl.appendChild(chip);
    });

    // Match Liquid build_markup pairing: full-span alone, else pack half-width into rows of 2.
    const rows = [];
    let i = 0;
    const items = layout.items;
    while (i < items.length) {
      const item = items[i];
      if (item.span >= 2) {
        rows.push([item]);
        i += 1;
        continue;
      }
      if (i + 1 < items.length && items[i + 1].span < 2) {
        rows.push([item, items[i + 1]]);
        i += 2;
      } else {
        rows.push([item]);
        i += 1;
      }
    }

    const stage = document.createElement("div");
    stage.className = `trmnl-stage${device === "v2" ? " is-v2" : ""}`;
    const layoutEl = document.createElement("div");
    layoutEl.className = "trmnl-eink-layout";

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "trmnl-canvas-empty";
      empty.textContent = "Drag components here";
      layoutEl.appendChild(empty);
    }

    let flatIndex = 0;
    rows.forEach((row) => {
      if (row.length === 1 && row[0].span >= 2) {
        const item = row[0];
        const tile = document.createElement("div");
        tile.className = "trmnl-tile is-full";
        tile.dataset.blockId = item.id;
        tile.dataset.index = String(flatIndex);
        tile.innerHTML = tileChrome(item, { removable: true });
        bindTileInteractions(tile, item, flatIndex);
        layoutEl.appendChild(tile);
        flatIndex += 1;
        return;
      }
      const pair = document.createElement("div");
      pair.className = "trmnl-eink-row";
      row.forEach((item) => {
        const tile = document.createElement("div");
        tile.className = "trmnl-tile";
        tile.dataset.blockId = item.id;
        tile.dataset.index = String(flatIndex);
        tile.innerHTML = tileChrome(item, { removable: true });
        bindTileInteractions(tile, item, flatIndex);
        pair.appendChild(tile);
        flatIndex += 1;
      });
      layoutEl.appendChild(pair);
    });

    stage.appendChild(layoutEl);
    stage.insertAdjacentHTML("beforeend", titleBarHtml());
    canvasEl.innerHTML = "";
    canvasEl.appendChild(stage);
    syncFields();
  }

  function renderHousehold() {
    paletteEl.innerHTML = "";
    syncCanvasDevice();
    const v2 = device === "v2";
    canvasEl.innerHTML = `
      <div class="trmnl-stage${v2 ? " is-v2" : ""}">
        <div class="trmnl-household">
          <div class="trmnl-household-focus">
            <div class="trmnl-tile">
              <div class="trmnl-tile-head"><div class="trmnl-tile-title"><strong>Watch</strong></div></div>
              <div class="trmnl-preview-body">${previewBody("watch")}</div>
            </div>
            <div class="trmnl-tile">
              <div class="trmnl-tile-head"><div class="trmnl-tile-title"><strong>Apps</strong></div></div>
              <div class="trmnl-preview-body">${previewBody("apps")}</div>
            </div>
          </div>
          <div class="trmnl-household-apps">
            ${["newscast", "eventtrakr", "fileserve", "pinboard"].map(renderProductTile).join("")}
          </div>
          <div class="trmnl-stats-strip">
            <div><span class="trmnl-kicker">CPU</span><strong>${escapeHtml(preview.cpu_disp)}</strong></div>
            <div><span class="trmnl-kicker">RAM</span><strong>${escapeHtml(preview.mem_disp)}</strong></div>
            <div><span class="trmnl-kicker">Disk</span><strong>${escapeHtml(preview.disk_disp)}</strong></div>
            <div><span class="trmnl-kicker">Backup</span><strong>${escapeHtml(preview.backup_when)}</strong></div>
          </div>
        </div>
        ${titleBarHtml()}
      </div>`;
    syncFields();
    window.requestAnimationFrame(() => syncCanvasDevice());
  }

  function renderStatusWall() {
    paletteEl.innerHTML = "";
    syncCanvasDevice();
    const v2 = device === "v2";
    const apps = Array.isArray(preview.apps) ? preview.apps : [];
    const limit = v2 ? 8 : 6;
    const up = Number(preview.apps_up ?? apps.filter((a) => a.running).length);
    const total = Number(preview.apps_total ?? apps.length);
    const level = String(preview.watch_level || "attention").toLowerCase();
    let alertPill = '<span class="trmnl-pill">ATTENTION</span>';
    if (level === "healthy") alertPill = '<span class="trmnl-pill ok">OK</span>';
    else if (level === "critical") alertPill = '<span class="trmnl-pill solid">CRITICAL</span>';
    const backupPill = preview.backup_ok
      ? '<span class="trmnl-pill ok">OK</span>'
      : '<span class="trmnl-pill solid">FAILED</span>';
    const serviceRows = apps.slice(0, limit).map((app) => {
      const detail = escapeHtml(app.d || "—");
      const meta = app.m ? `<span class="trmnl-svc-meta">${escapeHtml(app.m)}</span>` : "";
      return `
        <div class="trmnl-svc-row">
          <div class="trmnl-svc-main">
            <strong>${escapeHtml(app.n)}</strong>
            <span class="trmnl-svc-detail">${detail}</span>
          </div>
          <div class="trmnl-svc-end">
            ${meta}
            <span class="trmnl-pill${app.running ? "" : " solid"}">${app.running ? "UP" : "DOWN"}</span>
          </div>
        </div>`;
    }).join("");
    canvasEl.innerHTML = `
      <div class="trmnl-stage${v2 ? " is-v2" : ""}">
        <div class="trmnl-statuswall">
          <div class="trmnl-status-head">
            <div class="trmnl-status-brand">
              <strong>${escapeHtml(preview.hostname || "stonepi")}</strong>
              <span class="trmnl-kicker">SYSTEM</span>
            </div>
            <span class="trmnl-pill dither">Refreshed ${escapeHtml(preview.refreshed_ago || "just now")}</span>
          </div>
          <div class="trmnl-stats-strip is-five">
            <div><span class="trmnl-kicker">CPU</span><strong>${escapeHtml(preview.cpu_disp)}</strong></div>
            <div><span class="trmnl-kicker">RAM</span><strong>${escapeHtml(preview.mem_disp)}</strong></div>
            <div><span class="trmnl-kicker">DISK</span><strong>${escapeHtml(preview.disk_disp)}</strong></div>
            <div><span class="trmnl-kicker">TEMP</span><strong>${escapeHtml(preview.temp_disp)}</strong></div>
            <div><span class="trmnl-kicker">UPTIME</span><strong>${escapeHtml(preview.uptime)}</strong></div>
          </div>
          <div class="trmnl-status-body">
            <div class="trmnl-tile trmnl-services">
              <div class="trmnl-tile-head">
                <div class="trmnl-tile-title"><strong>SERVICES</strong></div>
                <span class="trmnl-kicker">${up} of ${total} up</span>
              </div>
              <div class="trmnl-preview-body">${serviceRows || '<span class="trmnl-kicker">No apps</span>'}</div>
            </div>
            <div class="trmnl-status-side">
              <div class="trmnl-tile">
                <div class="trmnl-tile-head"><div class="trmnl-tile-title"><strong>ALERTS</strong></div></div>
                <div class="trmnl-preview-body">
                  <div class="trmnl-row">${alertPill}<span>${escapeHtml(preview.watch_summary || "—")}</span></div>
                </div>
              </div>
              <div class="trmnl-tile">
                <div class="trmnl-tile-head"><div class="trmnl-tile-title"><strong>STORAGE &amp; BACKUP</strong></div></div>
                <div class="trmnl-preview-body">
                  <div class="trmnl-storage-grid">
                    <div><span class="trmnl-kicker">DISK USED</span><strong>${escapeHtml(preview.disk_disp)}</strong></div>
                    <div>
                      <span class="trmnl-kicker">LAST BACKUP</span>
                      <div class="trmnl-row"><strong>${escapeHtml(preview.backup_when)}</strong>${backupPill}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>`;
    syncFields();
    // Remeasure after the fixed-design grid collapses to one column.
    window.requestAnimationFrame(() => syncCanvasDevice());
  }

  function render() {
    if (fixedDesign) {
      if (design === "status") renderStatusWall();
      else renderHousehold();
      return;
    }
    renderFreeform();
  }

  function placeAt(id, index) {
    const existing = layout.items.findIndex((item) => item.id === id);
    let span = 1;
    if (existing >= 0) {
      span = layout.items[existing].span;
      layout.items.splice(existing, 1);
      if (existing < index) index -= 1;
    }
    layout.items.splice(Math.max(0, Math.min(index, layout.items.length)), 0, { id, span });
    dragId = null;
    dragFrom = null;
    render();
  }

  canvasEl.addEventListener("dragover", (event) => {
    if (fixedDesign) return;
    event.preventDefault();
    canvasEl.classList.add("is-dragover");
  });
  canvasEl.addEventListener("dragleave", () => canvasEl.classList.remove("is-dragover"));
  canvasEl.addEventListener("drop", (event) => {
    if (fixedDesign) return;
    event.preventDefault();
    canvasEl.classList.remove("is-dragover");
    const id = dragId || event.dataTransfer.getData("text/plain");
    if (!id || !byId[id]) return;
    if (layout.items.some((item) => item.id === id)) {
      placeAt(id, layout.items.length);
      return;
    }
    layout.items.push({ id, span: 1 });
    dragId = null;
    dragFrom = null;
    render();
  });

  copyBtn?.addEventListener("click", async () => {
    const text = markupEl?.value || buildMarkup();
    const label = copyBtn.querySelector("span");
    async function writeClipboard(value) {
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(value);
          return true;
        } catch {
          /* insecure context / permission — fall through */
        }
      }
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;";
      document.body.appendChild(area);
      area.focus();
      area.select();
      area.setSelectionRange(0, area.value.length);
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      area.remove();
      return ok;
    }
    const ok = await writeClipboard(text);
    if (ok) {
      if (label) label.textContent = "Copied";
      setTimeout(() => {
        if (label) label.textContent = "Copy markup";
      }, 1600);
      return;
    }
    if (markupEl) {
      markupEl.focus();
      markupEl.select();
    }
    if (label) label.textContent = "Select + Ctrl+C";
    setTimeout(() => {
      if (label) label.textContent = "Copy markup";
    }, 2400);
  });

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => render(), 120);
  });

  render();
})();

(function vaultForm() {
  const form = document.querySelector("[data-vault-form]");
  if (!form) return;
  const select = form.querySelector("[data-vault-key]");
  const customWrap = form.querySelector("[data-vault-custom]");
  const customInput = form.querySelector("[data-vault-custom-input]");
  const blurb = form.querySelector("[data-vault-blurb]");

  function sync() {
    const value = select?.value || "";
    const isCustom = value === "__custom__";
    if (customWrap) customWrap.hidden = !isCustom;
    if (customInput) {
      customInput.required = isCustom;
      if (!isCustom) customInput.value = "";
    }
    const option = select?.selectedOptions?.[0];
    const text = option?.dataset?.blurb || "";
    if (blurb) {
      blurb.hidden = !text || isCustom || !value;
      blurb.textContent = text;
    }
  }

  select?.addEventListener("change", sync);
  sync();
})();

(function flashBanner() {
  const flash = document.querySelector("[data-flash]");
  if (!flash) return;
  flash.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("msg") || url.searchParams.has("err")) {
      url.searchParams.delete("msg");
      url.searchParams.delete("err");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
  } catch {
    /* ignore */
  }
})();

(function addUserPanel() {
  const panel = document.getElementById("add-user");
  if (!(panel instanceof HTMLDetailsElement)) return;

  function openPanel() {
    panel.open = true;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const first = panel.querySelector('input[name="username"]');
    if (first instanceof HTMLElement) {
      window.setTimeout(() => first.focus(), 50);
    }
  }

  document.querySelectorAll("[data-open-add-user]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      openPanel();
    });
  });

  panel.querySelectorAll("[data-close-add-user]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      panel.open = false;
    });
  });
})();

(function wireStudioPublishImpliesFileServe() {
  function sync(form) {
    if (!(form instanceof HTMLFormElement)) return;
    const publish = form.querySelector("[data-studio-can-publish]");
    const fileserve = form.querySelector("[data-app-fileserve]");
    if (!(publish instanceof HTMLInputElement) || !(fileserve instanceof HTMLInputElement)) return;
    if (publish.checked && !fileserve.disabled) {
      fileserve.checked = true;
    }
  }

  document.querySelectorAll("form.people-form").forEach((form) => {
    form.addEventListener("change", (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.matches("[data-studio-can-publish]")) {
        sync(form);
      }
    });
    sync(form);
  });
})();

function showFlash(kind, text) {
  const main = document.querySelector("main");
  if (!main || !text) return;
  document.querySelectorAll("[data-flash]").forEach((el) => el.remove());
  const el = document.createElement("div");
  const isError = kind === "error";
  el.className = "flash " + (isError ? "flash-error" : "flash-ok");
  el.setAttribute("role", isError ? "alert" : "status");
  el.dataset.flash = "";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", isError ? "M6 6l12 12M18 6L6 18" : "M5 12l4 4 10-10");
  svg.appendChild(path);
  const span = document.createElement("span");
  span.textContent = text;
  el.append(svg, span);
  main.insertBefore(el, main.firstChild);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

(function availabilityToggles() {
  document.querySelectorAll("form[data-availability-toggle]").forEach((form) => {
    const input = form.querySelector('input[type="checkbox"]');
    if (!input) return;
    form.addEventListener("submit", (event) => event.preventDefault());
    input.addEventListener("change", async () => {
      const enabled = input.checked;
      const name = form.dataset.appName || "this app";
      if (!enabled) {
        const ok = await askConfirm({
          title: "Hide " + name + "?",
          body: "Disabled apps are hidden from grants and blocked at sign-in. Dashboard stays available.",
          okLabel: "Hide",
        });
        if (!ok) {
          input.checked = true;
          return;
        }
      }
      const fd = new FormData(form);
      fd.set("enabled", enabled ? "1" : "0");
      input.disabled = true;
      try {
        const res = await fetch(form.action, {
          method: "POST",
          body: fd,
          headers: { Accept: "application/json", "X-Requested-With": "fetch" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          input.checked = !enabled;
          showFlash("error", data.error || "Could not save availability.");
          return;
        }
        form.closest(".service-card")?.classList.toggle("is-disabled", !enabled);
        showFlash("ok", data.message || "Availability saved");
      } catch {
        input.checked = !enabled;
        showFlash("error", "Could not save availability.");
      } finally {
        input.disabled = false;
      }
    });
  });
})();

(function launcherOrder() {
  const form = document.querySelector("[data-launcher-form]");
  if (!form) return;
  const grid = form.querySelector("[data-launcher-grid]");
  const orderInput = form.querySelector("[data-launcher-order]");
  const toggle = document.querySelector("[data-launcher-edit-toggle]");
  const lede = document.querySelector("[data-launcher-lede]");
  const editBar = form.querySelector("[data-launcher-edit-bar]");
  const cancel = form.querySelector("[data-launcher-cancel]");
  const ledeDefault = lede ? lede.textContent : "";
  let editing = false;
  let saving = false;
  let dragTile = null;
  let persistTimer = 0;
  let longPressTimer = 0;
  let longPressTile = null;
  let longPressMoved = false;
  let suppressClick = false;
  const LONG_PRESS_MS = 500;
  const MOVE_CANCEL_PX = 10;

  function tiles() {
    return Array.from(grid.querySelectorAll("[data-launcher-tile]"));
  }

  function syncOrderField() {
    const ids = tiles().map((tile) => tile.dataset.appId).filter(Boolean);
    if (orderInput) orderInput.value = ids.join(",");
    return ids;
  }

  function syncChrome() {
    const items = tiles();
    items.forEach((tile, index) => {
      const controls = tile.querySelector("[data-launcher-reorder]");
      const pos = tile.querySelector("[data-launcher-pos]");
      if (pos) pos.textContent = String(index + 1);
      if (controls) {
        controls.hidden = !editing;
        controls.setAttribute("aria-hidden", editing ? "false" : "true");
      }
      tile.draggable = editing;
      tile.classList.toggle("is-draggable", editing);
    });
  }

  function setEditing(on) {
    editing = Boolean(on);
    form.classList.toggle("is-editing", editing);
    if (editBar) editBar.hidden = !editing;
    if (toggle) toggle.setAttribute("aria-pressed", editing ? "true" : "false");
    if (lede) {
      lede.textContent = editing
        ? "Drag tiles to reorder — saves as you go."
        : ledeDefault;
    }
    if (!editing) {
      dragTile = null;
      tiles().forEach((tile) => tile.classList.remove("is-dragging", "is-drop-target", "is-longpress"));
    }
    syncChrome();
  }

  function clearLongPress() {
    window.clearTimeout(longPressTimer);
    longPressTimer = 0;
    if (longPressTile) longPressTile.classList.remove("is-longpress");
    longPressTile = null;
    longPressMoved = false;
  }

  function schedulePersist() {
    window.clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => persist(), 180);
  }

  function persist() {
    const ids = syncOrderField();
    if (!ids.length || saving) return;
    saving = true;
    const fd = new FormData(form);
    fd.set("order", ids.join(","));
    fetch(form.action, {
      method: "POST",
      body: fd,
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    })
      .catch(() => {})
      .finally(() => {
        saving = false;
      });
  }

  function placeBefore(target, before) {
    if (!dragTile || !target || dragTile === target) return;
    if (before) grid.insertBefore(dragTile, target);
    else grid.insertBefore(dragTile, target.nextSibling);
    syncOrderField();
    syncChrome();
  }

  function pointerPoint(event) {
    if (event.touches && event.touches[0]) {
      return { x: event.touches[0].clientX, y: event.touches[0].clientY };
    }
    return { x: event.clientX, y: event.clientY };
  }

  grid.addEventListener("pointerdown", (event) => {
    if (editing || event.button > 0) return;
    const targetTile = event.target.closest("[data-launcher-tile]");
    if (!targetTile) return;
    longPressTile = targetTile;
    longPressMoved = false;
    const start = pointerPoint(event);
    longPressTimer = window.setTimeout(() => {
      if (!longPressTile || longPressMoved) return;
      longPressTile.classList.add("is-longpress");
      suppressClick = true;
      try {
        if (navigator.vibrate) navigator.vibrate(12);
      } catch {
        /* ignore */
      }
      setEditing(true);
      clearLongPress();
    }, LONG_PRESS_MS);
    const onMove = (moveEvent) => {
      const pt = pointerPoint(moveEvent);
      if (Math.abs(pt.x - start.x) > MOVE_CANCEL_PX || Math.abs(pt.y - start.y) > MOVE_CANCEL_PX) {
        longPressMoved = true;
        clearLongPress();
        cleanup();
      }
    };
    const onUp = () => {
      clearLongPress();
      cleanup();
    };
    const cleanup = () => {
      grid.removeEventListener("pointermove", onMove);
      grid.removeEventListener("pointerup", onUp);
      grid.removeEventListener("pointercancel", onUp);
    };
    grid.addEventListener("pointermove", onMove);
    grid.addEventListener("pointerup", onUp);
    grid.addEventListener("pointercancel", onUp);
  });

  grid.addEventListener(
    "click",
    (event) => {
      if (!suppressClick) return;
      event.preventDefault();
      event.stopPropagation();
      suppressClick = false;
    },
    true
  );

  grid.addEventListener("contextmenu", (event) => {
    if (event.target.closest("[data-launcher-tile]")) event.preventDefault();
  });

  grid.addEventListener("dragstart", (event) => {
    if (!editing) return;
    const tile = event.target.closest("[data-launcher-tile]");
    if (!tile) return;
    dragTile = tile;
    tile.classList.add("is-dragging");
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", tile.dataset.appId || "");
    } catch {
      /* ignore */
    }
  });

  grid.addEventListener("dragend", () => {
    if (dragTile) dragTile.classList.remove("is-dragging");
    tiles().forEach((tile) => tile.classList.remove("is-drop-target"));
    dragTile = null;
    schedulePersist();
  });

  grid.addEventListener("dragover", (event) => {
    if (!editing || !dragTile) return;
    event.preventDefault();
    const over = event.target.closest("[data-launcher-tile]");
    if (!over || over === dragTile) return;
    const rect = over.getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2;
    placeBefore(over, before);
    try {
      event.dataTransfer.dropEffect = "move";
    } catch {
      /* ignore */
    }
  });

  grid.addEventListener("drop", (event) => {
    if (!editing) return;
    event.preventDefault();
    schedulePersist();
  });

  toggle?.addEventListener("click", () => setEditing(!editing));
  cancel?.addEventListener("click", () => setEditing(false));
  form.addEventListener("submit", (event) => {
    if (!editing) return;
    event.preventDefault();
    persist();
    setEditing(false);
  });
  setEditing(false);
})();

/* Settings → Network: Tailscale connect + auth wait polling */
(function initNetworkPanel() {
  const panel = document.querySelector("[data-network-panel]");
  if (!panel) return;

  const csrf =
    panel.getAttribute("data-csrf") ||
    document.querySelector('input[name="csrf_token"]')?.value ||
    readCookie("stonepi_csrf") ||
    "";

  function headers(extra) {
    const h = Object.assign({ Accept: "application/json", "X-Requested-With": "fetch" }, extra || {});
    if (csrf) h["X-StonePi-CSRF"] = csrf;
    return h;
  }

  async function postForm(url, fields) {
    const body = new FormData();
    body.set("csrf_token", csrf);
    Object.entries(fields || {}).forEach(([k, v]) => body.set(k, v));
    const res = await fetch(url, { method: "POST", headers: headers(), body, credentials: "same-origin" });
    let data = null;
    try {
      data = await res.json();
    } catch (_err) {
      data = null;
    }
    if (!res.ok) {
      const msg = (data && data.error) || "Request failed";
      throw new Error(msg);
    }
    return data || {};
  }

  async function fetchStatus() {
    const res = await fetch("/api/network/status", {
      headers: headers(),
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error("Status failed");
    return res.json();
  }

  const statusLabel = panel.querySelector("[data-ts-status-label]");
  const statusDot = panel.querySelector("[data-ts-dot]");
  const connectBlock = panel.querySelector("[data-ts-connect-block]");
  const authWait = panel.querySelector("[data-ts-auth-wait]");
  const authLink = panel.querySelector("[data-ts-auth-link]");
  const authWrap = panel.querySelector("[data-ts-auth-link-wrap]");
  const noLink = panel.querySelector("[data-ts-no-link]");
  const waitMsg = panel.querySelector("[data-ts-wait-msg]");
  const wantedInput = panel.querySelector("[data-ts-wanted]");
  const wantedLabel = panel.querySelector("[data-ts-wanted-label]");
  const connectBtn = panel.querySelector("[data-ts-connect-btn]");

  let pollTimer = null;
  let connecting = false;

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function setWantedLabel(on) {
    if (wantedLabel) wantedLabel.textContent = on ? "Enabled" : "Disabled";
  }

  function applyStatus(snap) {
    const ts = (snap && snap.tailscale) || {};
    let label = "Disconnected";
    let dot = "bad";
    if (!snap.appliance) {
      label = "Available on the Pi appliance";
      dot = "bad";
    } else if (ts.connected) {
      label = "Connected";
      dot = "ok";
    } else if (!ts.wanted) {
      label = "Disabled";
      dot = "bad";
    } else if (ts.needs_login || ts.auth_url || connecting) {
      label = connecting && !ts.auth_url ? "Starting…" : "Waiting for authentication";
      dot = "warn";
    } else if (!ts.installed) {
      label = "Not installed";
      dot = "bad";
    } else if (ts.wanted) {
      label = "Not connected";
      dot = "bad";
    }
    if (statusLabel) statusLabel.textContent = label;
    if (statusDot) {
      statusDot.classList.remove("ok", "bad", "warn");
      statusDot.classList.add(dot);
    }
    if (wantedInput && typeof ts.wanted === "boolean" && !wantedInput.disabled) {
      wantedInput.checked = ts.wanted;
      setWantedLabel(ts.wanted);
    }
    if (connectBlock) connectBlock.hidden = !ts.wanted;
    const showWait = Boolean(
      ts.wanted && !ts.connected && (ts.needs_login || ts.auth_url || connecting)
    );
    if (authWait) authWait.hidden = !showWait;
    if (showWait && waitMsg) {
      if (ts.auth_url) {
        waitMsg.textContent =
          "Waiting for authentication… Open the link below on your phone or computer, then approve this device.";
      } else if (connecting) {
        waitMsg.textContent = "Starting Tailscale… fetching your login link.";
      } else {
        waitMsg.textContent =
          "Waiting for authentication… No login link yet — tap Connect Tailscale again to generate one.";
      }
    }
    if (authLink && ts.auth_url) {
      authLink.href = ts.auth_url;
      authLink.textContent = ts.auth_url;
      if (authWrap) authWrap.hidden = false;
      if (noLink) noLink.hidden = true;
    } else if (authWrap) {
      authWrap.hidden = true;
      if (noLink) {
        noLink.hidden = !(showWait && !connecting);
        if (!noLink.hidden && ts.error) {
          noLink.innerHTML =
            "Still no link? Tap <strong>Connect Tailscale</strong> again.<br /><span data-ts-error></span>";
          const errEl = noLink.querySelector("[data-ts-error]");
          if (errEl) errEl.textContent = ts.error + (ts.backend_state ? " (" + ts.backend_state + ")" : "");
        }
      }
    }
    if (ts.connected) {
      connecting = false;
      stopPoll();
      const dns = panel.querySelector("[data-ts-dns]");
      const dnsDot = panel.querySelector("[data-ts-dns-dot]");
      const ipv4 = panel.querySelector("[data-ts-ipv4]");
      if (dnsDot) {
        dnsDot.classList.toggle("ok", Boolean(ts.magicdns_name || ts.magicdns));
        dnsDot.classList.toggle("bad", !(ts.magicdns_name || ts.magicdns));
      }
      if (dns) {
        const name = ts.magicdns_name
          ? "<code>" + ts.magicdns_name + "</code>"
          : ts.magicdns
            ? "On"
            : "Off";
        dns.innerHTML =
          '<span class="pill compact"><span class="dot ' +
          (ts.magicdns_name || ts.magicdns ? "ok" : "bad") +
          '" data-ts-dns-dot></span> ' +
          name +
          "</span>";
      }
      if (ipv4 && ts.ipv4) ipv4.innerHTML = "<code>" + ts.ipv4 + "</code>";
      if (!panel.querySelector("[data-ts-disconnect-form]")) {
        window.location.reload();
      }
    } else if (ts.wanted && (ts.needs_login || ts.auth_url || connecting)) {
      startPoll();
    }
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      if (document.hidden) return;
      fetchStatus().then(applyStatus).catch(() => {});
    }, 2000);
  }

  const wantedForm = panel.querySelector("[data-ts-wanted-form]");
  wantedForm?.addEventListener("submit", (event) => event.preventDefault());
  wantedInput?.addEventListener("change", async () => {
    const on = Boolean(wantedInput.checked);
    setWantedLabel(on);
    if (!on) {
      const ok =
        typeof askConfirm === "function"
          ? await askConfirm({
              title: "Disable remote access?",
              body: "StonePi stays on your LAN. Tailscale remote access will be turned off until you enable it again.",
              okLabel: "Disable",
            })
          : window.confirm("Disable remote access?");
      if (!ok) {
        wantedInput.checked = true;
        setWantedLabel(true);
        return;
      }
    }
    wantedInput.disabled = true;
    try {
      const snap = await postForm("/settings/network/tailscale/wanted", { wanted: on ? "on" : "off" });
      applyStatus(snap);
      if (!on) stopPoll();
    } catch (err) {
      wantedInput.checked = !on;
      setWantedLabel(!on);
      if (typeof showFlash === "function") {
        showFlash("error", err.message || "Could not save remote access.");
      }
    } finally {
      wantedInput.disabled = false;
    }
  });

  panel.querySelector("[data-ts-connect-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    connecting = true;
    if (connectBtn) connectBtn.disabled = true;
    if (authWait) authWait.hidden = false;
    if (authWrap) authWrap.hidden = true;
    if (noLink) noLink.hidden = true;
    if (waitMsg) waitMsg.textContent = "Starting Tailscale… fetching your login link.";
    if (statusLabel) statusLabel.textContent = "Starting…";
    if (statusDot) {
      statusDot.classList.remove("ok", "bad", "warn");
      statusDot.classList.add("warn");
    }
    startPoll();
    try {
      const snap = await postForm("/settings/network/tailscale/connect", {});
      connecting = false;
      applyStatus(snap);
      if (snap.tailscale && snap.tailscale.auth_url && /^https:\/\//.test(snap.tailscale.auth_url)) {
        try {
          window.open(snap.tailscale.auth_url, "_blank", "noopener,noreferrer");
        } catch (_err) {
          /* ignore popup block */
        }
      } else if (snap.tailscale && snap.tailscale.error && waitMsg) {
        waitMsg.textContent = snap.tailscale.error;
        if (noLink) noLink.hidden = false;
      }
      startPoll();
    } catch (err) {
      connecting = false;
      if (waitMsg) {
        waitMsg.textContent = err.message || "Connect failed. Try again.";
      }
      if (noLink) noLink.hidden = false;
    }
    if (connectBtn) connectBtn.disabled = false;
  });

  panel.querySelector("[data-ts-disconnect-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await postForm("/settings/network/tailscale/disconnect", {});
      window.location.reload();
    } catch (_err) {
      event.target.submit();
    }
  });

  fetchStatus()
    .then((snap) => {
      const ts = snap.tailscale || {};
      if (ts.wanted && !ts.connected && (ts.needs_login || ts.auth_url)) {
        applyStatus(snap);
        startPoll();
      }
    })
    .catch(() => {});
})();

const settingsShell = document.querySelector("[data-settings-shell]");
if (settingsShell) {
  const DESKTOP_MQ = window.matchMedia("(min-width: 1024px)");
  const hubList = settingsShell.querySelector("[data-settings-hub-list]");
  const panelList = settingsShell.querySelector("[data-settings-panel-list]");
  const panelListRows = settingsShell.querySelector("[data-settings-panel-list-rows]");
  const sectionEl = settingsShell.querySelector("[data-settings-section]");
  const settingsBack = document.querySelector("[data-settings-hub-back]");
  const settingsTitle = document.querySelector("[data-settings-title]");
  const settingsLede = document.querySelector("[data-settings-lede]");
  const hubLede = settingsShell.dataset.settingsHubLede || "Household portal settings.";

  function readJson(selector, fallback) {
    const node = settingsShell.querySelector(selector);
    if (!node?.textContent) return fallback;
    try {
      return JSON.parse(node.textContent);
    } catch (_err) {
      return fallback;
    }
  }

  const panelsByTab = readJson("[data-settings-panels-json]", {});
  const ledesByTab = readJson("[data-settings-ledes-json]", {});
  const labelsByTab = readJson("[data-settings-labels-json]", {});

  function isDesktop() {
    return DESKTOP_MQ.matches;
  }

  function tabHasPanels(tab) {
    return (panelsByTab[tab] || []).length > 1;
  }

  function syncDesktopClass() {
    settingsShell.classList.toggle("is-desktop-settings", isDesktop());
  }

  function updateBack(mode, tab) {
    if (!settingsBack) return;
    if (mode === "hub" || isDesktop()) {
      settingsBack.hidden = true;
      settingsBack.dataset.backTo = "hub";
      settingsBack.textContent = "← Settings";
      return;
    }
    settingsBack.hidden = false;
    if (mode === "form" && tabHasPanels(tab)) {
      settingsBack.dataset.backTo = "panelList";
      settingsBack.textContent = `← ${labelsByTab[tab] || "Back"}`;
    } else {
      settingsBack.dataset.backTo = "hub";
      settingsBack.textContent = "← Settings";
    }
  }

  function renderPanelList(tab) {
    if (!panelListRows) return;
    panelListRows.innerHTML = "";
    (panelsByTab[tab] || []).forEach((panel) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "settings-hub-row settings-panel-row";
      row.dataset.settingsPanelRow = panel.id;
      row.innerHTML =
        `<span class="settings-hub-icon" aria-hidden="true"></span>` +
        `<span class="settings-hub-copy">` +
        `<span class="settings-hub-title"></span>` +
        `<span class="settings-hub-sub"></span>` +
        `</span>` +
        `<span class="settings-hub-chevron" aria-hidden="true">›</span>`;
      const iconHost = row.querySelector(".settings-hub-icon");
      const iconSrc = document.querySelector(
        `[data-settings-panel-icon="${tab}-${panel.id}"]`
      );
      if (iconHost && iconSrc) {
        iconHost.innerHTML = iconSrc.innerHTML;
      }
      row.querySelector(".settings-hub-title").textContent = panel.label;
      row.querySelector(".settings-hub-sub").textContent =
        panel.subtext || "Open this section";
      panelListRows.appendChild(row);
    });
  }

  function applyPanelCards(tab, panelId) {
    const panels = panelsByTab[tab] || [];
    const active = panels.find((p) => p.id === panelId) || panels[0] || null;
    const cards = new Set(active?.cards || []);
    const filterCards = panels.length > 1 && !isDesktop();
    settingsShell.querySelectorAll("[data-settings-panel-card]").forEach((card) => {
      const inTab = card.closest("[data-settings-panel]")?.dataset.settingsPanel === tab;
      const hide = filterCards && inTab && cards.size > 0 && !cards.has(card.dataset.settingsPanelCard);
      card.classList.toggle("is-panel-hidden", hide);
    });
    settingsShell.dataset.settingsActivePanel = active?.id || "";
    return active?.id || null;
  }

  function setMode({ hub = false, panelListMode = false } = {}) {
    const onHub = Boolean(hub) && !isDesktop();
    const onList = Boolean(panelListMode) && !isDesktop() && !onHub;
    settingsShell.dataset.settingsHub = onHub ? "true" : "false";
    settingsShell.dataset.settingsPanelList = onList ? "true" : "false";
    settingsShell.classList.toggle("is-hub", onHub);
    settingsShell.classList.toggle("is-panel-list", onList);
    if (hubList) hubList.hidden = !onHub;
    if (panelList) panelList.hidden = !onList;
    if (sectionEl) sectionEl.hidden = (onHub || onList) && !isDesktop();
  }

  function showSettings(tab, { hub = false, panel = null, panelListMode = false } = {}) {
    const next = labelsByTab[tab] ? tab : "general";
    const showHub = Boolean(hub) && !isDesktop();
    const showList = Boolean(panelListMode) && !isDesktop() && !showHub && tabHasPanels(next);
    setMode({ hub: showHub, panelListMode: showList });
    settingsShell.dataset.settingsActiveTab = next;

    if (showHub) {
      if (settingsTitle) settingsTitle.textContent = "Settings";
      if (settingsLede) settingsLede.textContent = hubLede;
      updateBack("hub", next);
    } else if (showList) {
      renderPanelList(next);
      if (settingsTitle) settingsTitle.textContent = labelsByTab[next] || next;
      if (settingsLede) settingsLede.textContent = "Tap a section to edit it.";
      updateBack("panelList", next);
    } else {
      const panels = panelsByTab[next] || [];
      const active = panels.find((p) => p.id === panel) || panels[0];
      if (settingsTitle) {
        settingsTitle.textContent =
          !isDesktop() && active?.label ? active.label : labelsByTab[next] || next;
      }
      if (settingsLede) settingsLede.textContent = ledesByTab[next] || "";
      updateBack("form", next);
    }

    let applied = null;
    if (!showHub && !showList) {
      applied = applyPanelCards(next, panel);
    }

    const url = new URL(window.location.href);
    if (showHub) {
      url.searchParams.delete("tab");
      url.searchParams.delete("panel");
    } else {
      url.searchParams.set("tab", next);
      if (showList || !applied) url.searchParams.delete("panel");
      else url.searchParams.set("panel", applied);
    }
    window.history.replaceState(null, "", url);
  }

  settingsShell.querySelectorAll("[data-settings-hub-row]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = row.dataset.settingsHubRow;
      // Always navigate — hub HTML only includes the default tab's panels.
      window.location.href = `/settings?tab=${encodeURIComponent(tab)}`;
    });
  });

  if (panelList) {
    panelList.addEventListener("click", (event) => {
      const row = event.target.closest("[data-settings-panel-row]");
      if (!row || !panelList.contains(row)) return;
      event.preventDefault();
      const tab = settingsShell.dataset.settingsActiveTab || "general";
      const panel = row.dataset.settingsPanelRow;
      window.location.href = `/settings?tab=${encodeURIComponent(tab)}&panel=${encodeURIComponent(panel)}`;
    });
  }

  if (settingsBack) {
    settingsBack.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = settingsShell.dataset.settingsActiveTab || "general";
      if (settingsBack.dataset.backTo === "panelList") {
        showSettings(tab, { panelListMode: true });
      } else {
        window.location.href = "/settings";
      }
    });
  }

  syncDesktopClass();
  const initialHub = settingsShell.dataset.settingsHub === "true";
  const url = new URL(window.location.href);
  const urlPanel = url.searchParams.get("panel");
  const hash = (window.location.hash || "").replace(/^#/, "");
  const hashPanel =
    hash === "account-password" || hash === "view-options" || hash === "appearance" || hash === "remote-access"
      ? { "account-password": "password", "view-options": "view", appearance: "appearance", "remote-access": "remote" }[hash]
      : null;

  if (isDesktop()) {
    if (initialHub) {
      window.location.replace("/settings?tab=general");
      // keep page usable until navigate
      setMode({ hub: false });
      applyPanelCards("general", null);
    } else {
      setMode({ hub: false });
      applyPanelCards(settingsShell.dataset.settingsActiveTab || "general", urlPanel);
      updateBack("form", settingsShell.dataset.settingsActiveTab || "general");
    }
  } else if (initialHub) {
    updateBack("hub", "general");
  } else {
    const tab = settingsShell.dataset.settingsActiveTab || "general";
    // Only an explicit ?panel= / hash opens a section; otherwise show the L2 list.
    const panel = urlPanel || hashPanel || null;
    if (tabHasPanels(tab) && !panel) {
      showSettings(tab, { panelListMode: true });
    } else {
      setMode({ hub: false });
      applyPanelCards(tab, panel);
      updateBack("form", tab);
      if (!isDesktop() && tabHasPanels(tab)) {
        const panels = panelsByTab[tab] || [];
        const active = panels.find((p) => p.id === panel) || panels[0];
        if (settingsTitle && active?.label) settingsTitle.textContent = active.label;
      }
    }
  }

  if (typeof DESKTOP_MQ.addEventListener === "function") {
    DESKTOP_MQ.addEventListener("change", () => {
      syncDesktopClass();
      window.location.reload();
    });
  }
}
