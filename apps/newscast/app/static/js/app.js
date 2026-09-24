function t(k) {
  return (window.NEWSCAST_I18N && window.NEWSCAST_I18N[k]) || k;
}

function syncTopbarHeight() {
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--topbar-height", `${height}px`);
}
syncTopbarHeight();
window.addEventListener("resize", syncTopbarHeight);
window.addEventListener("orientationchange", syncTopbarHeight);
window.addEventListener("pageshow", syncTopbarHeight);
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncTopbarHeight);
}
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(syncTopbarHeight);
}
if (typeof ResizeObserver === "function") {
  const topbar = document.querySelector(".topbar");
  if (topbar) new ResizeObserver(syncTopbarHeight).observe(topbar);
}

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
    button.classList.toggle("is-active", button.dataset.paletteSet === chosen);
  });
}

const savedTheme = readShared(THEME_KEY, LEGACY_THEME_KEYS, "system");
applyTheme(savedTheme);
document.querySelectorAll("[data-theme-set]").forEach((button) => {
  button.addEventListener("click", () => {
    persistPref(THEME_KEY, button.dataset.themeSet);
    applyTheme(button.dataset.themeSet);
  });
});
document.querySelectorAll("[data-palette-set]").forEach((button) => {
  button.addEventListener("click", () => {
    persistPref(PALETTE_KEY, button.dataset.paletteSet);
    applyTheme(readShared(THEME_KEY, LEGACY_THEME_KEYS, "system"), button.dataset.paletteSet);
  });
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (readShared(THEME_KEY, LEGACY_THEME_KEYS, "system") === "system") applyTheme("system");
});

const toastEl = document.querySelector("[data-toast]");
const TOAST_KEY = "newscast-toast";
const TOAST_MS = 4500;

function toast(message, kind) {
  if (!toastEl || !message) return;
  toastEl.textContent = message;
  toastEl.classList.remove("is-ok", "is-error", "is-on");
  if (kind === "error") toastEl.classList.add("is-error");
  else toastEl.classList.add("is-ok");
  toastEl.hidden = false;
  window.requestAnimationFrame(() => {
    toastEl.classList.add("is-on");
  });
  window.clearTimeout(toastEl._timer);
  toastEl._timer = window.setTimeout(() => {
    toastEl.classList.remove("is-on");
    toastEl.hidden = true;
  }, TOAST_MS);
}

function toastAfterReload(message, kind) {
  try {
    sessionStorage.setItem(TOAST_KEY, JSON.stringify({ message, kind: kind || "ok" }));
  } catch {
    toast(message, kind);
  }
}

try {
  const pending = sessionStorage.getItem(TOAST_KEY);
  if (pending) {
    sessionStorage.removeItem(TOAST_KEY);
    const data = JSON.parse(pending);
    toast(data.message, data.kind);
  }
} catch {
  /* ignore a bad stored toast */
}

function appPrefix() {
  const raw = document.documentElement.getAttribute("data-stonepi-prefix") || "";
  return raw.replace(/\/$/, "");
}

function withPrefix(path) {
  if (!path || typeof path !== "string") return path;
  // form.action is always an absolute URL; unwrap same-origin paths so we can
  // re-apply /news (etc.) when the HTML action was root-absolute (/feeds/...).
  try {
    if (/^https?:\/\//i.test(path)) {
      const absolute = new URL(path);
      if (absolute.origin === window.location.origin) {
        path = absolute.pathname + absolute.search + absolute.hash;
      } else {
        return path;
      }
    }
  } catch {
    /* keep original */
  }
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  const prefix = appPrefix();
  if (!prefix) return path;
  if (path === prefix || path.startsWith(prefix + "/")) return path;
  return prefix + path;
}

function onLoginPath() {
  const path = window.location.pathname || "";
  const prefix = appPrefix();
  if (prefix && (path === prefix + "/login" || path.startsWith(prefix + "/login/"))) return true;
  return path === "/login" || path.startsWith("/login/");
}

async function send(url, options = {}) {
  const response = await fetch(withPrefix(url), {
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "X-Requested-With": "fetch",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (response.status === 401 && !onLoginPath()) {
    window.location.href = withPrefix("/login");
    throw new Error("Signed out");
  }
  const data = await response.json().catch(() => ({ ok: response.ok, message: response.statusText }));
  if (!response.ok) {
    throw new Error(data.message || data.detail || "Request failed");
  }
  return data;
}

function applyIngestStatus(data) {
  const refreshBtn = document.querySelector("[data-refresh]");
  const running = Boolean(data.running);
  if (refreshBtn) {
    refreshBtn.disabled = running;
    refreshBtn.classList.toggle("is-busy", running);
  }
  document.querySelectorAll("[data-feed-refresh]").forEach((button) => {
    button.disabled = running;
    button.classList.toggle("is-busy", running);
  });
  if (ingestWasRunning && !running && document.querySelector("[data-briefing-list]")) {
    ingestWasRunning = false;
    window.location.reload();
    return;
  }
  ingestWasRunning = running;
}

function setActivityBanner(text, { busy = false, error = false } = {}) {
  const banner = document.querySelector("[data-activity-banner]");
  if (!banner) return;
  const value = (text || "").trim();
  if (!value) {
    banner.hidden = true;
    banner.textContent = "";
    banner.classList.remove("is-busy", "is-error");
    return;
  }
  banner.hidden = false;
  banner.textContent = value;
  banner.classList.toggle("is-busy", busy);
  banner.classList.toggle("is-error", error);
}

function applyActivity(data) {
  const ingest = data.ingest || {};
  const reader = data.reader || {};
  const recent = data.recent_sync || null;
  applyIngestStatus(ingest);

  if (ingest.running) {
    const progress = ingest.progress ? `${t("Refreshing")} ${ingest.progress}…` : `${t("Refreshing")}…`;
    setActivityBanner(progress, { busy: true });
  } else if (reader.pending > 0) {
    const label = reader.active_label || t("reader");
    const copy =
      reader.pending === 1
        ? `${t("Sending")} ${label}…`
        : `${t("Sending")} ${reader.pending} ${t("files")}…`;
    setActivityBanner(copy, { busy: true });
  } else if (ingest.last_error) {
    setActivityBanner(ingest.last_error, { error: true });
  } else {
    setActivityBanner("");
  }

  const ingestKey = `${ingest.running ? 1 : 0}|${ingest.last_message || ""}|${ingest.last_error || ""}`;
  if (activityState.ingestKey && activityState.ingestKey !== ingestKey && activityState.ingestRunning && !ingest.running) {
    if (ingest.last_error) toast(ingest.last_error, "error");
    else if (ingest.last_message) toast(ingest.last_message, "ok");
    const created = Number(ingest.last_new_stories || 0);
    const onBriefing = /^\/(\?|$)/.test(window.location.pathname + window.location.search) || window.location.pathname === "/";
    if (created > 0 && onBriefing) {
      window.setTimeout(() => window.location.reload(), 600);
    }
  }
  activityState.ingestRunning = Boolean(ingest.running);
  activityState.ingestKey = ingestKey;

  const recentKey = recent ? `${recent.task_id}|${recent.status}` : "";
  if (recentKey && recentKey !== activityState.recentKey) {
    if (activityState.recentKey) {
      if (recent.status === "failed") {
        toast(recent.error ? `${t("Couldn’t send")} ${recent.label}: ${recent.error}` : `${t("Couldn’t send")} ${recent.label}`, "error");
      } else if (recent.status === "complete") {
        toast(`${t("Sent")} ${recent.label}.`, "ok");
      }
    }
    activityState.recentKey = recentKey;
  } else if (!activityState.recentKey && recentKey) {
    activityState.recentKey = recentKey;
  }
}

async function pollActivity() {
  try {
    applyActivity(await send("/api/activity"));
  } catch {
    try {
      applyIngestStatus(await send("/api/ingest/status"));
    } catch {
      /* stay on last rendered state */
    }
  }
}

const activityState = {
  ingestRunning: Boolean(document.querySelector("[data-refresh]")?.classList.contains("is-busy")),
  ingestKey: "",
  recentKey: "",
};
let ingestWasRunning = activityState.ingestRunning;
window.setInterval(pollActivity, 4000);
pollActivity();


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
    if (okBtn) okBtn.textContent = okLabel || t("Remove");
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

function showFormError(form, message) {
  const errorEl = form.querySelector("[data-form-error]");
  if (!errorEl) return;
  errorEl.textContent = message || "";
  errorEl.hidden = !message;
}

document.querySelectorAll("form").forEach((form) => {
  if (form.dataset.bound || form.dataset.native != null) return;
  form.dataset.bound = "1";
  form.addEventListener("submit", async (event) => {
    if (form.method.toLowerCase() !== "post") return;
    event.preventDefault();
    if (form.dataset.confirm) {
      const ok = await askConfirm({
        title: form.dataset.confirm,
        body: form.dataset.confirmDetail || "",
        okLabel: form.dataset.confirmOk || t("Remove"),
      });
      if (!ok) return;
    }
    const body = new FormData(form);
    const button = form.querySelector("[type=submit]");
    const originalLabel = button?.textContent;
    showFormError(form, "");
    if (button) {
      button.disabled = true;
      if (form.dataset.busyLabel) button.textContent = form.dataset.busyLabel;
    }
    try {
      const action = form.getAttribute("action") || form.action;
      const data = await send(action, { method: "POST", body });
      if (data.reauth) {
        window.location.href = withPrefix("/login");
        return;
      }
      toastAfterReload(data.message || "Saved", "ok");
      document.querySelectorAll("[data-filter-root]").forEach((root) => writeStoredFilters(root));
      window.location.reload();
    } catch (error) {
      showFormError(form, error.message);
      toast(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        if (originalLabel) button.textContent = originalLabel;
      }
    }
  });
});

function findSheet(name) {
  if (name) {
    return document.querySelector(`[data-sheet="${name}"]`);
  }
  return document.querySelector("[data-sheet]");
}

/** Sheets inside scrolling `.main` break `position:fixed` on iOS — hoist to body. */
function mountSheetsToBody() {
  document.querySelectorAll(".sheet").forEach((sheet) => {
    if (sheet.parentElement !== document.body) {
      document.body.appendChild(sheet);
    }
  });
}
mountSheetsToBody();

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

function syncFilterPicker(sheet) {
  if (!sheet) return;
  const root =
    document.querySelector("[data-filter-root]") ||
    document;
  const group = [...root.querySelectorAll("[data-chip-group]")].find(
    (item) => (item.dataset.filterKey || "category") === "category"
  );
  const active = group?.querySelector("[data-filter].is-active")?.dataset.filter || "all";
  sheet.querySelectorAll("[data-filter-pick]").forEach((option) => {
    const on = option.dataset.filterPick === active;
    option.classList.toggle("is-active", on);
    option.setAttribute("aria-selected", on ? "true" : "false");
  });
}

function applyFilterPick(option) {
  const value = option.dataset.filterPick;
  if (!value) return;
  const filterKey = option.dataset.filterKey || "category";
  const sheet = option.closest("[data-sheet]");
  const root =
    document.querySelector("[data-filter-root]") ||
    document;
  const group = [...root.querySelectorAll("[data-chip-group]")].find(
    (item) => (item.dataset.filterKey || "category") === filterKey
  );
  if (!group) return;
  const chip = [...group.querySelectorAll("[data-filter]")].find((item) => item.dataset.filter === value);
  if (!chip) return;
  group.querySelectorAll("[data-filter]").forEach((other) => other.classList.toggle("is-active", other === chip));
  applyChipFilters(root);
  writeStoredFilters(root);
  resetFilterScroll();
  requestAnimationFrame(resetFilterScroll);
  closeSheetEl(sheet);
}

document.querySelectorAll("[data-open-sheet]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const name = btn.getAttribute("data-open-sheet") || "";
    const sheet = findSheet(name);
    if (name === "filters") syncFilterPicker(sheet);
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

document.querySelectorAll("[data-filter-pick]").forEach((option) => {
  option.addEventListener("click", () => applyFilterPick(option));
});

function openFeedFromHash() {
  const match = /^#feed-(\d+)$/.exec(window.location.hash || "");
  if (!match) return;
  const row = document.getElementById(`feed-${match[1]}`);
  if (!row) return;
  const details = row.matches("details") ? row : row.querySelector("details");
  if (details) details.open = true;
  row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

openFeedFromHash();
window.addEventListener("hashchange", openFeedFromHash);

document.querySelectorAll("[data-story-expand]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest(".story-card");
    if (!card) return;
    const open = !card.classList.contains("is-expanded");
    card.classList.toggle("is-expanded", open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
    button.setAttribute("aria-label", open ? "Collapse story" : "Expand story");
  });
});

document.querySelectorAll("[data-keep-days]").forEach((select) => {
  const custom = select.closest("form")?.querySelector("[data-custom-expiry]");
  if (!custom) return;
  const sync = () => {
    custom.hidden = select.value !== "0";
  };
  select.addEventListener("change", sync);
  sync();
});

document.querySelectorAll("[data-schedule-mode]").forEach((select) => {
  const custom = select.closest("form")?.querySelector("[data-custom-interval]");
  if (!custom) return;
  const sync = () => {
    custom.hidden = select.value !== "custom";
  };
  select.addEventListener("change", sync);
  sync();
});

const modelSelect = document.querySelector("[data-model-select]");
const modelCustom = document.querySelector("[data-model-custom]");
if (modelSelect && modelCustom) {
  const syncModel = () => {
    modelCustom.hidden = modelSelect.value !== "other";
  };
  modelSelect.addEventListener("change", syncModel);
  syncModel();
}

document.querySelectorAll("[data-llm-provider]").forEach((select) => {
  const form = select.closest("form");
  if (!form) return;
  const sync = () => {
    form.querySelectorAll("[data-provider-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.providerPanel !== select.value;
    });
  };
  select.addEventListener("change", sync);
  sync();
});

document.querySelectorAll("[data-reader-device]").forEach((select) => {
  const form = select.closest("form");
  if (!form) return;
  const host = form.querySelector("[data-reader-host]");
  const folder = form.querySelector("[data-reader-folder]");
  const defaults = {
    xteink: { host: "crosspoint.local", folder: "/News" },
    kobo: { host: "192.168.1.50", folder: "/mnt/onboard/News" },
  };
  const sync = () => {
    const device = select.value;
    form.querySelectorAll("[data-reader-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.readerPanel !== device;
    });
    form.querySelectorAll("[data-reader-hint]").forEach((hint) => {
      hint.hidden = hint.dataset.readerHint !== device;
    });
    const next = defaults[device] || defaults.xteink;
    const prev = device === "kobo" ? defaults.xteink : defaults.kobo;
    if (host) {
      host.placeholder = next.host;
      if (!host.value.trim() || host.value.trim() === prev.host) {
        host.value = device === "kobo" ? "" : next.host;
      }
    }
    if (folder) {
      folder.placeholder = next.folder;
      if (!folder.value.trim() || folder.value.trim() === prev.folder) {
        folder.value = next.folder;
      }
    }
  };
  select.addEventListener("change", sync);
  sync();
});

document.querySelectorAll("[data-paper-naming]").forEach((root) => {
  const form = root.closest("form");
  const patternInput = root.querySelector("[data-paper-pattern]");
  const categoryPatternInput = root.querySelector("[data-paper-category-pattern]");
  const labelInput = root.querySelector("[data-paper-label]");
  const dateSelect = root.querySelector("[data-paper-date-format]");
  const preview = root.querySelector("[data-paper-preview]");
  const categoryPreview = root.querySelector("[data-paper-category-preview]");
  if (!patternInput || !preview) return;
  const hostInput = form?.querySelector("[name=device_hostname]");
  const instanceInput = form?.querySelector("[name=instance_name]");
  const defaultPattern = "NewsCast - {hostname} {instance} {date}";
  const defaultCategoryPattern = "NewsCast - {hostname} {instance} {category} {date}";

  function collapseName(value) {
    let text = String(value || "")
      .replace(/\s+/g, " ")
      .trim();
    text = text.replace(/(?:\s*-\s*){2,}/g, " - ");
    text = text.replace(/^\s*-\s*|\s*-\s*$/g, "");
    return text.replace(/\s+/g, " ").trim().replace(/^-+|-+$/g, "").trim();
  }

  function dateSample() {
    const option = dateSelect?.selectedOptions?.[0];
    return option?.dataset.paperDateSample || "";
  }

  function tokenValues(extra = {}) {
    return {
      product: "NewsCast",
      hostname: (hostInput?.value || "").trim(),
      instance: (instanceInput?.value || "").trim(),
      label: (labelInput?.value || "").trim(),
      category: "Tech",
      date: dateSample(),
      ...extra,
    };
  }

  function fillPattern(pattern, fallback, values) {
    const source = (pattern || "").trim() || fallback;
    const filled = source.replace(
      /\{(product|hostname|instance|label|category|date)\}/gi,
      (_, key) => values[key.toLowerCase()] || "",
    );
    return collapseName(filled) || `NewsCast ${values.date}`.trim();
  }

  function renderPreview() {
    const values = tokenValues();
    preview.textContent = fillPattern(patternInput.value, defaultPattern, values);
    if (categoryPreview && categoryPatternInput) {
      categoryPreview.textContent = fillPattern(categoryPatternInput.value, defaultCategoryPattern, values);
    }
  }

  function insertToken(input, token) {
    if (!input) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    const needsSpaceBefore = before.length && !/\s$/.test(before) && !/-$/.test(before);
    const needsSpaceAfter = after.length && !/^\s/.test(after) && !/^-/.test(after);
    const chunk = `${needsSpaceBefore ? " " : ""}${token}${needsSpaceAfter ? " " : ""}`;
    const next = `${before}${chunk}${after}`.slice(0, 120);
    input.value = next;
    const cursor = Math.min(before.length + chunk.length, next.length);
    input.focus();
    input.setSelectionRange(cursor, cursor);
    renderPreview();
  }

  root.querySelectorAll("[data-paper-token]").forEach((button) => {
    button.addEventListener("click", () => insertToken(patternInput, button.dataset.paperToken || ""));
  });
  root.querySelectorAll("[data-paper-category-token]").forEach((button) => {
    button.addEventListener("click", () => insertToken(categoryPatternInput, button.dataset.paperCategoryToken || ""));
  });
  [patternInput, categoryPatternInput, labelInput, dateSelect, hostInput, instanceInput].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", renderPreview);
    el.addEventListener("change", renderPreview);
  });
  renderPreview();
});

const loadOllama = document.querySelector("[data-load-ollama]");
if (loadOllama) {
  const form = loadOllama.closest("form");
  const modelInput = form?.querySelector("[data-ollama-model]");
  const pick = form?.querySelector("[data-ollama-pick]");
  const modelSelectOllama = form?.querySelector("[data-ollama-select]");
  loadOllama.addEventListener("click", async () => {
    const base = form?.querySelector("[name=ollama_base_url]")?.value || "";
    loadOllama.disabled = true;
    try {
      const data = await send("/api/ollama/models?base_url=" + encodeURIComponent(base));
      const models = data.models || [];
      if (!models.length) {
        toast("Ollama is running, but no models are installed.", "error");
        return;
      }
      if (modelSelectOllama && pick) {
        modelSelectOllama.innerHTML = '<option value="">Choose…</option>';
        models.forEach((name) => {
          const option = document.createElement("option");
          option.value = name;
          option.textContent = name;
          if (modelInput && modelInput.value === name) option.selected = true;
          modelSelectOllama.appendChild(option);
        });
        pick.hidden = false;
      }
      toast(`Found ${models.length} model${models.length === 1 ? "" : "s"}.`, "ok");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      loadOllama.disabled = false;
    }
  });
  modelSelectOllama?.addEventListener("change", () => {
    if (modelInput && modelSelectOllama.value) modelInput.value = modelSelectOllama.value;
  });
}

function applyChipFilters(root) {
  const selected = {};
  root.querySelectorAll("[data-chip-group]").forEach((group) => {
    const key = group.dataset.filterKey || "category";
    selected[key] = group.querySelector("[data-filter].is-active")?.dataset.filter || "all";
  });
  const category = selected.category || "all";
  const source = selected.source || "all";
  let visible = 0;
  const items = root.querySelectorAll("[data-filter-item]");
  if (items.length) {
    items.forEach((item) => {
      const hide =
        (category !== "all" && item.dataset.category !== category) ||
        (source !== "all" && item.dataset.source !== source);
      item.hidden = hide;
      if (!hide) visible += 1;
    });
    root.querySelectorAll("[data-filter-section]").forEach((section) => {
      const any = [...section.querySelectorAll("[data-filter-item]")].some((item) => !item.hidden);
      section.hidden = !any;
    });
  } else {
    root.querySelectorAll("[data-category]").forEach((item) => {
      if (item.matches("[data-filter-section]")) return;
      const hide =
        category !== "all" &&
        (category === "favourites" ? item.dataset.favourited !== "1" : item.dataset.category !== category);
      item.hidden = hide;
      if (!hide) visible += 1;
    });
    root.querySelectorAll("[data-filter-section]").forEach((section) => {
      const items = section.querySelectorAll("[data-category]:not([data-filter-section])");
      if (!items.length) {
        const hide =
          category !== "all" &&
          section.dataset.category &&
          section.dataset.category !== category;
        section.hidden = hide;
        return;
      }
      const any = [...items].some((item) => !item.hidden);
      section.hidden = !any;
    });
  }
  const empty = root.querySelector("[data-filter-empty]");
  if (empty) empty.hidden = visible > 0;
}

function filterStoreKey() {
  return `newscast-filters:${window.location.pathname}`;
}

function readStoredFilters() {
  try {
    const raw = sessionStorage.getItem(filterStoreKey());
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function writeStoredFilters(root) {
  const selected = {};
  root.querySelectorAll("[data-chip-group]").forEach((group) => {
    const key = group.dataset.filterKey || "category";
    selected[key] = group.querySelector("[data-filter].is-active")?.dataset.filter || "all";
  });
  try {
    sessionStorage.setItem(filterStoreKey(), JSON.stringify(selected));
  } catch {
    /* ignore quota / private mode */
  }
}

function restoreStoredFilters(root) {
  const stored = readStoredFilters();
  root.querySelectorAll("[data-chip-group]").forEach((group) => {
    const key = group.dataset.filterKey || "category";
    const wanted = stored[key];
    if (!wanted) return;
    const chip = [...group.querySelectorAll("[data-filter]")].find((item) => item.dataset.filter === wanted);
    if (!chip) return;
    group.querySelectorAll("[data-filter]").forEach((other) => other.classList.toggle("is-active", other === chip));
  });
  applyChipFilters(root);
}

function resetFilterScroll() {
  const main = document.querySelector(".main");
  if (main) main.scrollTop = 0;
  window.scrollTo(0, 0);
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
}

document.querySelectorAll("[data-filter-root]").forEach((root) => {
  restoreStoredFilters(root);
});

document.querySelectorAll("[data-chip-group]").forEach((group) => {
  const chips = group.querySelectorAll("[data-filter]");
  const root = group.closest("[data-filter-root]") || document;
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((other) => other.classList.toggle("is-active", other === chip));
      applyChipFilters(root);
      writeStoredFilters(root);
      resetFilterScroll();
      requestAnimationFrame(resetFilterScroll);
    });
  });
});

const settingsRoot = document.querySelector("[data-settings-tabs]");
if (settingsRoot) {
  const SETTINGS_SAVE_TABS = new Set(["device", "schedule", "publication", "filters", "translation", "llm", "reader", "notifications", "update"]);
  const DESKTOP_MQ = window.matchMedia("(min-width: 1024px)");
  const settingsForm = settingsRoot.querySelector("[data-settings]");
  const settingsLede = document.querySelector("[data-settings-lede]");
  const settingsTitle = document.querySelector("[data-settings-title]");
  const settingsBack = document.querySelector("[data-settings-hub-back]");
  const settingsTabField = settingsRoot.querySelector("[data-settings-tab-field]");
  const settingsChips = settingsRoot.querySelectorAll("[data-settings-tab]");
  const hubList = settingsRoot.querySelector("[data-settings-hub-list]");
  const panelList = settingsRoot.querySelector("[data-settings-panel-list]");
  const panelListRows = settingsRoot.querySelector("[data-settings-panel-list-rows]");
  const sectionEl = settingsRoot.querySelector("[data-settings-section]");
  const sectionChips = settingsRoot.querySelector("[data-settings-section-chips]");
  const hubLede = settingsRoot.dataset.settingsHubLede || "Everything that shapes your paper, in one place.";

  function readJson(selector, fallback) {
    const node = settingsRoot.querySelector(selector);
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

  function isDesktopSettings() {
    return DESKTOP_MQ.matches;
  }

  function syncDesktopClass() {
    settingsRoot.classList.toggle("is-desktop-settings", isDesktopSettings());
  }

  function tabHasPanelList(tab) {
    return (panelsByTab[tab] || []).length > 0;
  }

  function updateBackControl(mode, tab) {
    if (!settingsBack) return;
    if (mode === "hub" || isDesktopSettings()) {
      settingsBack.hidden = true;
      settingsBack.dataset.backTo = "hub";
      settingsBack.textContent = "← Settings";
      return;
    }
    settingsBack.hidden = false;
    if (mode === "form" && tabHasPanelList(tab)) {
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
        `<span class="settings-hub-sub">Open this section</span>` +
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
      panelListRows.appendChild(row);
    });
  }

  function setViewMode({ hub = false, panelListMode = false } = {}) {
    const onHub = Boolean(hub) && !isDesktopSettings();
    const onPanelList = Boolean(panelListMode) && !isDesktopSettings() && !onHub;
    settingsRoot.dataset.settingsHub = onHub ? "true" : "false";
    settingsRoot.dataset.settingsPanelList = onPanelList ? "true" : "false";
    settingsRoot.classList.toggle("is-hub", onHub);
    settingsRoot.classList.toggle("is-panel-list", onPanelList);
    if (hubList) hubList.hidden = !onHub;
    if (panelList) panelList.hidden = !onPanelList;
    if (sectionEl) sectionEl.hidden = (onHub || onPanelList) && !isDesktopSettings();
  }

  function applySectionPanel(tab, panelId) {
    const panels = panelsByTab[tab] || [];
    const activePanel = panels.find((p) => p.id === panelId) || panels[0] || null;
    const activeCards = new Set(activePanel?.cards || []);
    const hasPanels = panels.length > 0 && !isDesktopSettings();

    if (sectionChips) {
      // Mobile uses the panel list; chips remain for any transitional layout and are hidden in CSS.
      sectionChips.hidden = !hasPanels || settingsRoot.classList.contains("is-panel-list");
      sectionChips.classList.toggle("is-empty", !hasPanels);
      sectionChips.innerHTML = "";
      if (hasPanels) {
        panels.forEach((panel) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = `chip-btn${panel.id === activePanel.id ? " is-active" : ""}`;
          btn.setAttribute("role", "tab");
          btn.dataset.settingsSectionPanel = panel.id;
          btn.dataset.settingsSectionCards = (panel.cards || []).join(",");
          btn.setAttribute("aria-selected", panel.id === activePanel.id ? "true" : "false");
          btn.textContent = panel.label;
          sectionChips.appendChild(btn);
        });
      }
    }

    settingsRoot.querySelectorAll("[data-settings-panel-card]").forEach((card) => {
      const cardId = card.dataset.settingsPanelCard;
      const inActiveTab = card.closest("[data-settings-panel]")?.dataset.settingsPanel === tab;
      const hide = hasPanels && inActiveTab && activeCards.size > 0 && !activeCards.has(cardId);
      card.classList.toggle("is-panel-hidden", hide);
    });

    settingsRoot.dataset.settingsActivePanel = activePanel?.id || "";
    return activePanel?.id || null;
  }

  function showSettingsTab(tab, { panel = null, hub = false, panelListMode = false } = {}) {
    const next = [...settingsChips].some((chip) => chip.dataset.settingsTab === tab) ? tab : "device";
    const showHub = Boolean(hub) && !isDesktopSettings();
    const showPanelList =
      Boolean(panelListMode) && !isDesktopSettings() && !showHub && tabHasPanelList(next);

    setViewMode({ hub: showHub, panelListMode: showPanelList });

    settingsChips.forEach((chip) => {
      const on = chip.dataset.settingsTab === next;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
    settingsRoot.querySelectorAll("[data-settings-panel]").forEach((panelEl) => {
      const isTab = panelEl.dataset.settingsPanel === next;
      if (showHub || showPanelList || !isTab) {
        panelEl.hidden = true;
        return;
      }
      panelEl.hidden = false;
    });
    if (settingsForm) settingsForm.hidden = showHub || showPanelList || !SETTINGS_SAVE_TABS.has(next);
    if (settingsTabField) settingsTabField.value = next;
    settingsRoot.dataset.settingsActiveTab = next;

    if (showHub) {
      if (settingsTitle) settingsTitle.textContent = "Settings";
      if (settingsLede) settingsLede.textContent = hubLede;
      updateBackControl("hub", next);
    } else if (showPanelList) {
      renderPanelList(next);
      if (settingsTitle) settingsTitle.textContent = labelsByTab[next] || next;
      if (settingsLede) settingsLede.textContent = "Tap a section to edit it.";
      updateBackControl("panelList", next);
    } else {
      if (settingsTitle) {
        const panels = panelsByTab[next] || [];
        const active = panels.find((p) => p.id === panel) || panels[0];
        settingsTitle.textContent =
          !isDesktopSettings() && active?.label ? active.label : labelsByTab[next] || next;
      }
      if (settingsLede) settingsLede.textContent = ledesByTab[next] || "";
      updateBackControl("form", next);
    }

    let panelId = panel;
    if (!showHub && !showPanelList) {
      if (!panelId || !(panelsByTab[next] || []).some((p) => p.id === panelId)) {
        panelId = (panelsByTab[next] || [])[0]?.id || null;
      }
    }
    const appliedPanel = showHub || showPanelList ? null : applySectionPanel(next, panelId);

    if (!showHub && !showPanelList && !isDesktopSettings() && tabHasPanelList(next)) {
      settingsRoot.querySelectorAll(`[data-settings-panel="${next}"]`).forEach((panelEl) => {
        const cards = panelEl.querySelectorAll("[data-settings-panel-card]");
        if (!cards.length) return;
        const anyVisible = [...cards].some((card) => !card.classList.contains("is-panel-hidden"));
        panelEl.hidden = !anyVisible;
      });
    }

    const url = new URL(window.location.href);
    if (showHub) {
      url.searchParams.delete("tab");
      url.searchParams.delete("panel");
    } else {
      url.searchParams.set("tab", next);
      if (showPanelList || !appliedPanel) url.searchParams.delete("panel");
      else url.searchParams.set("panel", appliedPanel);
    }
    window.history.replaceState(null, "", url);
    resetFilterScroll();
    requestAnimationFrame(resetFilterScroll);
  }

  settingsChips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = chip.dataset.settingsTab;
      if (!isDesktopSettings() && tabHasPanelList(tab)) {
        showSettingsTab(tab, { panelListMode: true });
      } else {
        showSettingsTab(tab, { hub: false });
      }
    });
  });

  settingsRoot.querySelectorAll("[data-settings-hub-row]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = row.dataset.settingsHubRow;
      if (!isDesktopSettings() && tabHasPanelList(tab)) {
        showSettingsTab(tab, { panelListMode: true });
      } else {
        showSettingsTab(tab, { hub: false });
      }
    });
  });

  if (panelList) {
    panelList.addEventListener("click", (event) => {
      const row = event.target.closest("[data-settings-panel-row]");
      if (!row || !panelList.contains(row)) return;
      event.preventDefault();
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
        hub: false,
        panel: row.dataset.settingsPanelRow,
      });
    });
  }

  if (settingsBack) {
    settingsBack.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = settingsRoot.dataset.settingsActiveTab || "device";
      if (settingsBack.dataset.backTo === "panelList") {
        showSettingsTab(tab, { panelListMode: true });
      } else {
        showSettingsTab(tab, { hub: true });
      }
    });
  }

  if (sectionChips) {
    sectionChips.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-settings-section-panel]");
      if (!btn || !sectionChips.contains(btn)) return;
      event.preventDefault();
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
        hub: false,
        panel: btn.dataset.settingsSectionPanel,
      });
    });
  }

  function onViewportChange() {
    syncDesktopClass();
    const hubAttr = settingsRoot.dataset.settingsHub === "true";
    const listAttr = settingsRoot.dataset.settingsPanelList === "true";
    if (isDesktopSettings()) {
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
        hub: false,
        panel: settingsRoot.dataset.settingsActivePanel || null,
      });
    } else if (hubAttr) {
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", { hub: true });
    } else if (listAttr) {
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", { panelListMode: true });
    } else {
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
        hub: false,
        panel: settingsRoot.dataset.settingsActivePanel || null,
      });
    }
  }

  syncDesktopClass();
  const initialHub = settingsRoot.dataset.settingsHub === "true";
  const urlPanel = new URL(window.location.href).searchParams.get("panel");
  if (isDesktopSettings()) {
    showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
      hub: false,
      panel: settingsRoot.dataset.settingsActivePanel || null,
    });
  } else if (initialHub) {
    // Hub HTML already visible.
    updateBackControl("hub", settingsRoot.dataset.settingsActiveTab || "device");
  } else {
    const tab = settingsRoot.dataset.settingsActiveTab || "device";
    if (tabHasPanelList(tab) && !urlPanel) {
      showSettingsTab(tab, { panelListMode: true });
    } else {
      showSettingsTab(tab, {
        hub: false,
        panel: settingsRoot.dataset.settingsActivePanel || null,
      });
    }
  }

  if (typeof DESKTOP_MQ.addEventListener === "function") {
    DESKTOP_MQ.addEventListener("change", onViewportChange);
  } else if (typeof DESKTOP_MQ.addListener === "function") {
    DESKTOP_MQ.addListener(onViewportChange);
  }
}

document.querySelectorAll("[data-dismiss-login-qr]").forEach((button) => {
  button.addEventListener("click", () => {
    document.getElementById("login-qr")?.remove();
  });
});

document.querySelectorAll("[data-check-set]").forEach((button) => {
  button.addEventListener("click", () => {
    const group = button.dataset.checkSet;
    const on = button.dataset.checkValue !== "0";
    document.querySelectorAll(`[data-check-group="${group}"] input[type="checkbox"]`).forEach((input) => {
      input.checked = on;
    });
  });
});

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  const input = button.closest(".password-field")?.querySelector("input");
  if (!input) return;
  const showIcon = button.querySelector("[data-icon-show]");
  const hideIcon = button.querySelector("[data-icon-hide]");
  button.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    button.title = show ? "Hide password" : "Show password";
    if (showIcon) showIcon.hidden = show;
    if (hideIcon) hideIcon.hidden = !show;
  });
});
