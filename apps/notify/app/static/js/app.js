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

applyTheme(readShared(THEME_KEY, LEGACY_THEME_KEYS, "system"));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (readShared(THEME_KEY, LEGACY_THEME_KEYS, "system") === "system") applyTheme("system");
});

function mountSheetsToBody() {
  document.querySelectorAll(".sheet").forEach((sheet) => {
    if (sheet.parentElement !== document.body) {
      document.body.appendChild(sheet);
    }
  });
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
  btn.addEventListener("click", () => {
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

mountSheetsToBody();

function resetScroll() {
  const main = document.querySelector(".main");
  if (main) main.scrollTop = 0;
  window.scrollTo(0, 0);
}

const settingsRoot = document.querySelector("[data-settings-tabs]");
if (settingsRoot) {
  const settingsChips = settingsRoot.querySelectorAll("[data-settings-tab]");
  const settingsLede = document.querySelector("[data-settings-lede]");
  const settingsTitle = document.querySelector("[data-settings-title]");
  const settingsBack = document.querySelector("[data-settings-hub-back]");
  const hubList = settingsRoot.querySelector("[data-settings-hub-list]");
  const sectionEl = settingsRoot.querySelector("[data-settings-section]");
  const hubLede = settingsRoot.dataset.settingsHubLede || "";

  function readJson(selector, fallback) {
    const node = settingsRoot.querySelector(selector);
    if (!node?.textContent) return fallback;
    try {
      return JSON.parse(node.textContent);
    } catch (_err) {
      return fallback;
    }
  }

  const ledesByTab = readJson("[data-settings-ledes-json]", {});
  const labelsByTab = readJson("[data-settings-labels-json]", {});

  function showSettingsTab(tab, { hub = false } = {}) {
    const next = [...settingsChips].some((chip) => chip.dataset.settingsTab === tab)
      ? tab
      : settingsChips[0]?.dataset.settingsTab || "destinations";
    const onHub = Boolean(hub);
    settingsRoot.classList.toggle("is-hub", onHub);
    settingsRoot.dataset.settingsHub = onHub ? "true" : "false";
    settingsRoot.dataset.settingsActiveTab = next;
    if (hubList) hubList.hidden = !onHub;
    if (sectionEl) sectionEl.hidden = onHub;
    if (settingsBack) settingsBack.hidden = onHub;

    settingsChips.forEach((chip) => {
      const on = chip.dataset.settingsTab === next;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
    settingsRoot.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      panel.hidden = onHub || panel.dataset.settingsPanel !== next;
    });
    if (settingsTitle) settingsTitle.textContent = onHub ? "Displays & Notifications" : labelsByTab[next] || next;
    if (settingsLede) settingsLede.textContent = onHub ? hubLede : ledesByTab[next] || "";

    const url = new URL(window.location.href);
    if (onHub) url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    window.history.replaceState(null, "", url);
    resetScroll();
  }

  const openTab = (tab) => (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    showSettingsTab(tab);
  };
  settingsChips.forEach((chip) => chip.addEventListener("click", openTab(chip.dataset.settingsTab)));
  settingsRoot.querySelectorAll("[data-settings-hub-row]").forEach((row) => {
    row.addEventListener("click", openTab(row.dataset.settingsHubRow));
  });
  if (settingsBack) {
    settingsBack.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      showSettingsTab(settingsRoot.dataset.settingsActiveTab || "destinations", { hub: true });
    });
  }
  showSettingsTab(settingsRoot.dataset.settingsActiveTab || "destinations", {
    hub: settingsRoot.dataset.settingsHub === "true",
  });
}
