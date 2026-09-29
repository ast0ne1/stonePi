function syncTopbarHeight() {
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--topbar-height", `${height}px`);
}

function resetScrollChrome() {
  window.scrollTo(0, 0);
  const main = document.querySelector(".main");
  if (main) main.scrollTop = 0;
}

syncTopbarHeight();
resetScrollChrome();
window.addEventListener("resize", syncTopbarHeight);
window.addEventListener("orientationchange", syncTopbarHeight);
window.addEventListener("pageshow", () => {
  syncTopbarHeight();
  resetScrollChrome();
});
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
}

const savedTheme = readShared(THEME_KEY, LEGACY_THEME_KEYS, "system");
applyTheme(savedTheme);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (readShared(THEME_KEY, LEGACY_THEME_KEYS, "system") === "system") applyTheme("system");
});

const toastEl = document.querySelector("[data-toast]");
const TOAST_KEY = "fileserve-toast";
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
  // re-apply /files (etc.) when the HTML action was root-absolute (/admin/...).
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

window.withPrefix = withPrefix;

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
  mountSheetsToBody();
  sheet.hidden = false;
  sheet.classList.add("is-open");
  document.documentElement.classList.add("sheet-open");
  document.body.classList.add("sheet-open");
}

function closeSheetEl(sheet) {
  sheet.hidden = true;
  sheet.classList.remove("is-open");
  if (!document.querySelector(".sheet.is-open")) {
    document.documentElement.classList.remove("sheet-open");
    document.body.classList.remove("sheet-open");
  }
}

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
    if (okBtn) okBtn.textContent = okLabel || "Remove";
    const finish = (value) => {
      closeSheetEl(sheet);
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
    openSheetEl(sheet);
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
        okLabel: form.dataset.confirmOk || "Remove",
      });
      if (!ok) return;
    }
    const body = new FormData(form);
    const button = form.querySelector("[type=submit]");
    const originalLabel = button?.innerHTML;
    showFormError(form, "");
    if (button) {
      button.disabled = true;
      if (form.dataset.busyLabel) button.textContent = form.dataset.busyLabel;
    }
    try {
      const data = await send(form.action, { method: "POST", body });
      if (data.reauth) {
        window.location.href = withPrefix("/login");
        return;
      }
      if (data.reveal) {
        try {
          sessionStorage.setItem("fileserve-reveal", JSON.stringify(data.reveal));
        } catch {
          /* ignore */
        }
      }
      toastAfterReload(data.message || "Saved", "ok");
      window.location.reload();
    } catch (error) {
      showFormError(form, error.message);
      toast(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        if (originalLabel) button.innerHTML = originalLabel;
      }
    }
  });
});

const settingsRoot = document.querySelector("[data-settings-tabs]");
if (settingsRoot) {
  const SETTINGS_SAVE_TABS = new Set(["device", "update"]);
  const settingsChips = settingsRoot.querySelectorAll("[data-settings-tab]");
  const settingsForm = settingsRoot.querySelector("[data-settings]");
  const settingsTabField = settingsRoot.querySelector("[data-settings-tab-field]");
  const settingsLede = document.querySelector("[data-settings-lede]");
  const settingsTitle = document.querySelector("[data-settings-title]");
  const settingsBack = document.querySelector("[data-settings-hub-back]");
  const hubList = settingsRoot.querySelector("[data-settings-hub-list]");
  const panelList = settingsRoot.querySelector("[data-settings-panel-list]");
  const panelListRows = settingsRoot.querySelector("[data-settings-panel-list-rows]");
  const sectionEl = settingsRoot.querySelector("[data-settings-section]");
  const hubLede = settingsRoot.dataset.settingsHubLede || "";

  function chipFor(tab) {
    return [...settingsChips].find((chip) => chip.dataset.settingsTab === tab);
  }

  function tabLabel(tab) {
    return chipFor(tab)?.querySelector("span")?.textContent?.trim() || tab;
  }

  function cardsFor(tab) {
    const panel = settingsRoot.querySelector(`[data-settings-panel="${tab}"]`);
    return panel ? [...panel.querySelectorAll("[data-settings-panel-card]")] : [];
  }

  function setMode({ hub = false, panelListMode = false } = {}) {
    settingsRoot.classList.toggle("is-hub", hub);
    settingsRoot.classList.toggle("is-panel-list", panelListMode);
    settingsRoot.dataset.settingsHub = hub ? "true" : "false";
    if (hubList) hubList.hidden = !hub;
    if (panelList) panelList.hidden = !panelListMode;
    if (sectionEl) sectionEl.hidden = hub || panelListMode;
    if (settingsBack) {
      settingsBack.hidden = hub;
      settingsBack.textContent = settingsBack.dataset.backTo === "panelList"
        ? `← ${tabLabel(settingsRoot.dataset.settingsActiveTab || "")}`
        : "← Settings";
    }
  }

  function renderPanelList(tab) {
    if (!panelListRows) return;
    panelListRows.innerHTML = "";
    cardsFor(tab).forEach((card) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "settings-hub-row";
      row.dataset.settingsPanelRow = card.dataset.settingsPanelCard;
      const icon = card.querySelector(".section-heading svg");
      row.innerHTML =
        `<span class="settings-hub-icon" aria-hidden="true"></span>` +
        `<span class="settings-hub-copy">` +
        `<span class="settings-hub-title"></span>` +
        `<span class="settings-hub-sub"></span>` +
        `</span>` +
        `<span class="settings-hub-chevron" aria-hidden="true">›</span>`;
      if (icon) row.querySelector(".settings-hub-icon").appendChild(icon.cloneNode(true));
      row.querySelector(".settings-hub-title").textContent = card.dataset.panelLabel || card.dataset.settingsPanelCard;
      row.querySelector(".settings-hub-sub").textContent = card.dataset.panelSub || "Open this section";
      panelListRows.appendChild(row);
    });
  }

  function applyCards(tab, panelId) {
    const cards = cardsFor(tab);
    if (cards.length < 2) {
      cards.forEach((card) => card.classList.remove("is-panel-hidden"));
      return null;
    }
    const ids = new Set(cards.map((card) => card.dataset.settingsPanelCard));
    const active = ids.has(panelId) ? panelId : cards[0].dataset.settingsPanelCard;
    cards.forEach((card) => {
      card.classList.toggle("is-panel-hidden", card.dataset.settingsPanelCard !== active);
    });
    const panel = settingsRoot.querySelector(`[data-settings-panel="${tab}"]`);
    panel?.querySelectorAll("[data-settings-with-card]").forEach((el) => {
      el.classList.toggle("is-panel-hidden", el.dataset.settingsWithCard !== active);
    });
    return active;
  }

  function showSettingsTab(tab, { hub = false, panel = null, panelListMode = false } = {}) {
    const next = chipFor(tab) ? tab : settingsChips[0]?.dataset.settingsTab || "device";
    const multi = cardsFor(next).length > 1;
    const showHub = Boolean(hub);
    const showList = !showHub && Boolean(panelListMode) && multi;
    settingsRoot.dataset.settingsActiveTab = next;
    if (settingsBack) settingsBack.dataset.backTo = !showHub && !showList && multi ? "panelList" : "hub";
    setMode({ hub: showHub, panelListMode: showList });

    settingsChips.forEach((chip) => {
      const on = chip.dataset.settingsTab === next;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
    settingsRoot.querySelectorAll("[data-settings-panel]").forEach((panelEl) => {
      panelEl.hidden = showHub || showList || panelEl.dataset.settingsPanel !== next;
    });
    if (settingsForm) settingsForm.hidden = showHub || showList || !SETTINGS_SAVE_TABS.has(next);
    if (settingsTabField) settingsTabField.value = next;

    const activeChip = chipFor(next);
    let applied = null;
    if (showHub) {
      if (settingsTitle) settingsTitle.textContent = "Settings";
      if (settingsLede) settingsLede.textContent = hubLede;
    } else if (showList) {
      renderPanelList(next);
      if (settingsTitle) settingsTitle.textContent = tabLabel(next);
      if (settingsLede) settingsLede.textContent = "Choose a section.";
    } else {
      applied = applyCards(next, panel);
      const activeCard = cardsFor(next).find((card) => card.dataset.settingsPanelCard === applied);
      if (settingsTitle) {
        settingsTitle.textContent = multi && activeCard?.dataset.panelLabel
          ? activeCard.dataset.panelLabel
          : tabLabel(next);
      }
      if (settingsLede && activeChip?.dataset.settingsLede) {
        settingsLede.textContent = activeChip.dataset.settingsLede;
      }
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

  settingsChips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = chip.dataset.settingsTab;
      if (cardsFor(tab).length > 1) showSettingsTab(tab, { panelListMode: true });
      else showSettingsTab(tab);
    });
  });

  settingsRoot.querySelectorAll("[data-settings-hub-row]").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const tab = row.dataset.settingsHubRow;
      if (cardsFor(tab).length > 1) showSettingsTab(tab, { panelListMode: true });
      else showSettingsTab(tab);
    });
  });

  panelList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-settings-panel-row]");
    if (!row || !panelList.contains(row)) return;
    event.preventDefault();
    showSettingsTab(settingsRoot.dataset.settingsActiveTab || "device", {
      panel: row.dataset.settingsPanelRow,
    });
  });

  settingsBack?.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const tab = settingsRoot.dataset.settingsActiveTab || "device";
    if (settingsBack.dataset.backTo === "panelList") showSettingsTab(tab, { panelListMode: true });
    else showSettingsTab(tab, { hub: true });
  });

  const initialHub = settingsRoot.dataset.settingsHub === "true";
  const urlPanel = new URL(window.location.href).searchParams.get("panel");
  if (initialHub) {
    setMode({ hub: true });
  } else {
    const tab = settingsRoot.dataset.settingsActiveTab || "device";
    if (cardsFor(tab).length > 1 && !urlPanel) showSettingsTab(tab, { panelListMode: true });
    else showSettingsTab(tab, { panel: urlPanel || settingsRoot.dataset.settingsActivePanel || null });
  }
}

const addTabsRoot = document.querySelector("[data-add-tabs]");
if (addTabsRoot) {
  const addChips = addTabsRoot.querySelectorAll("[data-add-tab]");

  function showAddTab(tab) {
    const next = tab === "url" ? "url" : "file";
    addChips.forEach((chip) => {
      const on = chip.dataset.addTab === next;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
    addTabsRoot.querySelectorAll("[data-add-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.addPanel !== next;
    });
    const path = next === "url" ? "/admin/add/url" : "/admin/add";
    window.history.replaceState(null, "", withPrefix(path));
  }

  addChips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      showAddTab(chip.dataset.addTab);
    });
  });
}

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

function slugify(value) {
  return (value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[/]+/g, " ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function syncExpiryFields(root) {
  const mode = root.querySelector("[data-expiry-mode]");
  const custom = root.querySelector("[data-expiry-custom]");
  const date = root.querySelector("[data-expiry-date]");
  const on = mode?.value === "custom";
  if (custom) custom.hidden = !on;
  if (date) date.required = on;
}

function syncProtectFields(root) {
  const toggle = root.querySelector("[data-protect-toggle]");
  const fields = root.querySelector("[data-protect-fields]");
  const on = Boolean(toggle?.checked);
  if (fields) fields.hidden = !on;
  const user = root.querySelector("[name=page_username]");
  const pass = root.querySelector("[name=page_password]");
  if (user) user.required = on;
  if (pass) {
    const keep = on && fields?.dataset.hasPassword === "1";
    pass.required = on && !keep;
  }
}

function bindPageSetup(root) {
  if (!root || root.dataset.setupBound) return;
  root.dataset.setupBound = "1";
  const label = root.querySelector("[data-label-field]");
  const slug = root.querySelector("[data-slug-field]");
  const toggle = root.querySelector("[data-protect-toggle]");
  const expiry = root.querySelector("[data-expiry-mode]");
  if (label && slug && slug.dataset.slugLocked == null) {
    slug.addEventListener("input", () => {
      slug.dataset.slugDirty = slug.value.trim() ? "1" : "";
    });
    label.addEventListener("input", () => {
      if (slug.dataset.slugDirty) return;
      slug.value = slugify(label.value);
    });
    if (!slug.value) slug.value = slugify(label.value);
  }
  toggle?.addEventListener("change", () => syncProtectFields(root));
  expiry?.addEventListener("change", () => syncExpiryFields(root));
  syncProtectFields(root);
  syncExpiryFields(root);
}

document.querySelectorAll("[data-page-setup]").forEach(bindPageSetup);

const editSheet = document.querySelector("[data-edit-sheet]");
const editForm = editSheet?.querySelector("[data-edit-form]");

function closeEditSheet() {
  if (!editSheet) return;
  closeSheetEl(editSheet);
}

function openEditSheet(button) {
  if (!editSheet || !editForm) return;
  const protectedOn = button.dataset.pageProtected === "1";
  editForm.action = withPrefix(`/admin/edit/${button.dataset.pageId}`);
  const titleEl = editSheet.querySelector("[data-edit-title]");
  if (titleEl) titleEl.textContent = `Edit ${button.dataset.pageLabel || "page"}`;
  const label = editForm.querySelector("[data-label-field]");
  const slug = editForm.querySelector("[data-slug-field]");
  const description = editForm.querySelector("[data-description-field]");
  const user = editForm.querySelector("[name=page_username]");
  const pass = editForm.querySelector("[name=page_password]");
  const toggle = editForm.querySelector("[data-protect-toggle]");
  const fields = editForm.querySelector("[data-protect-fields]");
  const expiry = editForm.querySelector("[data-expiry-mode]");
  const expiryDate = editForm.querySelector("[data-expiry-date]");
  const file = editForm.querySelector("[data-edit-file]");
  if (label) label.value = button.dataset.pageLabel || "";
  if (slug) slug.value = button.dataset.pageSlug || "";
  if (description) description.value = button.dataset.pageDescription || "";
  if (user) user.value = button.dataset.pageUsername || "";
  if (pass) {
    pass.value = "";
    pass.type = "password";
  }
  if (toggle) toggle.checked = protectedOn;
  if (fields) fields.dataset.hasPassword = protectedOn ? "1" : "";
  if (expiry) expiry.value = button.dataset.pageExpiry || "none";
  if (expiryDate) expiryDate.value = button.dataset.pageExpiryDate || "";
  if (file) {
    file.value = "";
    file.dispatchEvent(new Event("change"));
  }
  showFormError(editForm, "");
  syncProtectFields(editForm);
  syncExpiryFields(editForm);
  openSheetEl(editSheet);
  label?.focus();
}

document.querySelectorAll("[data-edit-page]").forEach((button) => {
  button.addEventListener("click", () => {
    button.closest(".hosted-more")?.removeAttribute("open");
    openEditSheet(button);
  });
});
editSheet?.querySelector("[data-close-edit]")?.addEventListener("click", closeEditSheet);
editSheet?.addEventListener("click", (event) => {
  if (event.target === editSheet) closeEditSheet();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (editSheet && !editSheet.hidden) {
    closeEditSheet();
    return;
  }
  const openShare = document.querySelector("[data-share-sheet]:not([hidden])");
  if (openShare) {
    closeSheetEl(openShare);
    return;
  }
  document.querySelectorAll(".hosted-more[open]").forEach((details) => {
    details.open = false;
  });
});

document.querySelectorAll("[data-file-picker]").forEach((input) => {
  const picker = input.closest(".file-picker");
  const nameEl = picker?.querySelector("[data-file-name]");
  const empty = nameEl?.dataset.empty || "Tap to choose a file";
  const sync = () => {
    const file = input.files && input.files[0];
    if (nameEl) nameEl.textContent = file ? file.name : empty;
    picker?.classList.toggle("has-file", Boolean(file));
  };
  input.addEventListener("change", sync);
  sync();
});

async function copyText(value) {
  const text = value || "";
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      toast("Copied", "ok");
      return;
    }
  } catch {
    /* fall through to a wider fallback */
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.left = "-9999px";
  document.body.appendChild(field);
  field.select();
  field.setSelectionRange(0, field.value.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  field.remove();
  toast(ok ? "Copied" : "Could not copy", ok ? "ok" : "error");
}

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", () => copyText(button.dataset.copy));
});

const shareSheet = document.querySelector("[data-share-sheet]");
const shareTitle = shareSheet?.querySelector("[data-share-title]");
const shareQr = shareSheet?.querySelector("[data-share-qr]");
const shareUrl = shareSheet?.querySelector("[data-share-url]");
const shareCopy = shareSheet?.querySelector("[data-share-copy]");
const shareDownload = shareSheet?.querySelector("[data-share-download]");
const sharePrint = shareSheet?.querySelector("[data-share-print]");

function closeShareSheet() {
  if (!shareSheet) return;
  closeSheetEl(shareSheet);
}

function openShareSheet(card) {
  if (!shareSheet || !card) return;
  const label = card.dataset.shareLabel || "page";
  const url = card.dataset.shareUrl || "";
  if (shareTitle) shareTitle.textContent = `Share ${label}`;
  if (shareQr) {
    shareQr.src = card.dataset.shareQr || "";
    shareQr.alt = `QR code for ${label}`;
  }
  if (shareUrl) shareUrl.textContent = url;
  if (shareCopy) shareCopy.dataset.copy = url;
  if (shareDownload) shareDownload.href = card.dataset.shareDownload || "#";
  if (sharePrint) sharePrint.href = card.dataset.sharePrint || "#";
  openSheetEl(shareSheet);
}

document.querySelectorAll("[data-share-page]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest("[data-page-card]");
    openShareSheet(card);
  });
});
shareSheet?.querySelector("[data-close-share]")?.addEventListener("click", closeShareSheet);
shareSheet?.addEventListener("click", (event) => {
  if (event.target === shareSheet) closeShareSheet();
});
shareCopy?.addEventListener("click", () => copyText(shareCopy.dataset.copy || shareUrl?.textContent || ""));

document.querySelectorAll(".hosted-more").forEach((details) => {
  details.addEventListener("toggle", () => {
    if (!details.open) return;
    document.querySelectorAll(".hosted-more[open]").forEach((other) => {
      if (other !== details) other.open = false;
    });
  });
});
document.addEventListener("click", (event) => {
  document.querySelectorAll(".hosted-more[open]").forEach((details) => {
    if (!details.contains(event.target)) details.open = false;
  });
});

const pageList = document.querySelector("[data-page-list]");
const searchInput = document.querySelector("[data-page-search]");
const sortSelect = document.querySelector("[data-page-sort]");
const searchEmpty = document.querySelector("[data-search-empty]");

function pageCards() {
  return [...(pageList?.querySelectorAll("[data-page-card]") || [])];
}

function applyPageFilters() {
  if (!pageList) return;
  const query = (searchInput?.value || "").trim().toLowerCase();
  const sort = sortSelect?.value || "newest";
  const cards = pageCards();
  let visible = 0;
  cards.forEach((card) => {
    const haystack = (card.dataset.search || "").toLowerCase();
    const match = !query || haystack.includes(query);
    card.hidden = !match;
    if (match) visible += 1;
  });
  const ordered = [...cards].sort((a, b) => {
    if (sort === "title") return (a.dataset.title || "").localeCompare(b.dataset.title || "");
    if (sort === "opened") return Number(b.dataset.opened || 0) - Number(a.dataset.opened || 0);
    if (sort === "opens") return Number(b.dataset.opens || 0) - Number(a.dataset.opens || 0);
    return Number(b.dataset.created || 0) - Number(a.dataset.created || 0);
  });
  ordered.forEach((card) => pageList.append(card));
  if (searchEmpty) searchEmpty.hidden = visible > 0 || !query;
}

searchInput?.addEventListener("input", applyPageFilters);
sortSelect?.addEventListener("change", applyPageFilters);
applyPageFilters();
