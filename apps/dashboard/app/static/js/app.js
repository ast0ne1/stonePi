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
  const allowed = VIEW_ALLOWED[set];
  const fallback = VIEW_DEFAULTS[set];
  let value = readCookie(key) || localStorage.getItem(key) || fallback;
  if (Array.isArray(allowed) && !allowed.includes(value)) value = fallback;
  return value;
}

function applyViewPrefsToDocument() {
  Object.keys(VIEW_DATASET).forEach((set) => {
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
    const allowed = VIEW_ALLOWED[set];
    if (Array.isArray(allowed) && !allowed.includes(value)) {
      value = VIEW_DEFAULTS[set];
    }
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

document.querySelectorAll("[data-app-color-swatch]").forEach((el) => {
  if (!(el instanceof HTMLInputElement)) return;
  el.addEventListener("input", () => {
    const row = el.closest(".app-color-row");
    const icon = row?.querySelector(".service-icon");
    if (icon instanceof HTMLElement) icon.style.setProperty("--tile-accent", el.value);
  });
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

// A control named "action" (hidden input or button) shadows form.action,
// so read the attribute instead.
function formUrl(form) {
  return form.getAttribute("action") || window.location.pathname;
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
  document.querySelectorAll("[data-flash]").forEach(autoDismissFlash);
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

(function usersAdmin() {
  const shell = document.querySelector("[data-users-shell]");
  if (!shell) return;

  const addSheet = document.querySelector("[data-add-user-sheet]");
  const resetSheet = document.querySelector("[data-reset-password-sheet]");
  const resetForm = document.querySelector("[data-reset-password-form]");
  let selectedId = shell.querySelector(".users-row.is-selected")?.getAttribute("data-user-select") || "";
  let resetUserId = "";
  let saveTimer = 0;

  function isDesktop() {
    return window.matchMedia("(min-width: 1024px)").matches;
  }

  function hoistSheet(sheet) {
    if (sheet && sheet.parentElement !== document.body) {
      document.body.appendChild(sheet);
    }
  }
  hoistSheet(addSheet);
  hoistSheet(resetSheet);

  function openSheet(sheet) {
    if (!sheet) return;
    hoistSheet(sheet);
    sheet.hidden = false;
    sheet.classList.add("is-open");
    document.documentElement.classList.add("sheet-open");
    document.body.classList.add("sheet-open");
  }

  function closeSheet(sheet) {
    if (!sheet) return;
    sheet.hidden = true;
    sheet.classList.remove("is-open");
    if (!document.querySelector(".sheet.is-open")) {
      document.documentElement.classList.remove("sheet-open");
      document.body.classList.remove("sheet-open");
    }
  }

  function setView(view) {
    shell.dataset.view = view;
  }

  function selectUser(id, { mobileDetail = true } = {}) {
    if (!id) return;
    selectedId = id;
    shell.querySelectorAll("[data-user-select]").forEach((row) => {
      const on = row.getAttribute("data-user-select") === id;
      row.classList.toggle("is-selected", on);
      row.setAttribute("aria-pressed", on ? "true" : "false");
    });
    shell.querySelectorAll("[data-user-detail]").forEach((panel) => {
      const on = panel.getAttribute("data-user-detail") === id;
      panel.classList.toggle("is-active", on);
      panel.hidden = !on;
    });
    if (!isDesktop() && mobileDetail) setView("detail");
    closeMoreMenus();
  }

  function syncStudioFileserve(form) {
    const publish = form.querySelector("[data-studio-can-publish]");
    const fileserve = form.querySelector("[data-app-fileserve]");
    if (!(publish instanceof HTMLInputElement) || !(fileserve instanceof HTMLInputElement)) return;
    if (publish.checked && !fileserve.disabled) fileserve.checked = true;
  }

  function syncCapsVisibility(form) {
    form.querySelectorAll("[data-ua-app]").forEach((row) => {
      const toggle = row.querySelector('input[name="apps"]');
      const caps = row.querySelector("[data-ua-caps]");
      if (!caps) return;
      const on = row.classList.contains("is-locked") || (toggle instanceof HTMLInputElement && toggle.checked);
      caps.classList.toggle("is-hidden", !on);
    });
  }

  function syncAdminLock(form) {
    const admin = form.querySelector('input[name="is_admin"][value="1"]');
    const isAdmin = admin instanceof HTMLInputElement && admin.checked;
    form.querySelectorAll('input[name="apps"]').forEach((input) => {
      if (!(input instanceof HTMLInputElement) || input.type === "hidden") return;
      const label = input.closest(".ua-toggle");
      if (isAdmin) {
        input.checked = true;
        input.setAttribute("data-admin-locked", "");
        label?.classList.add("is-locked");
      } else {
        input.removeAttribute("data-admin-locked");
        if (!input.disabled) label?.classList.remove("is-locked");
      }
    });
    form.querySelectorAll(".ua-cap").forEach((cap) => {
      const input = cap.querySelector('input[type="checkbox"]');
      cap.classList.toggle("is-locked", isAdmin);
      if (isAdmin && input instanceof HTMLInputElement) input.checked = true;
    });
    // Platform permissions admins always hold (e.g. Phone alerts).
    form.querySelectorAll("input[data-admin-on]").forEach((input) => {
      if (!(input instanceof HTMLInputElement)) return;
      if (isAdmin) input.checked = true;
      input.closest(".ua-toggle")?.classList.toggle("is-locked", isAdmin);
    });
    syncCapsVisibility(form);
    updateGrantCount(form);
  }

  function updateGrantCount(form) {
    const hint = form.closest("[data-user-detail]")?.querySelector("[data-grant-count]");
    if (!hint) return;
    const admin = form.querySelector('input[name="is_admin"][value="1"]');
    const isAdmin = admin instanceof HTMLInputElement && admin.checked;
    const total = form.querySelectorAll("[data-ua-app]").length;
    if (isAdmin) {
      hint.textContent = String(total);
      return;
    }
    let count = 0;
    form.querySelectorAll("[data-ua-app]").forEach((row) => {
      if (row.classList.contains("is-locked")) {
        count += 1;
        return;
      }
      const toggle = row.querySelector('input[name="apps"]');
      if (toggle instanceof HTMLInputElement && toggle.checked) count += 1;
    });
    hint.textContent = String(count);
  }

  function setSaveHint(form, state, text) {
    const el = form.closest("[data-user-detail]")?.querySelector("[data-save-hint]");
    if (!el) return;
    el.classList.remove("is-saving", "is-error");
    if (state === "saving") el.classList.add("is-saving");
    if (state === "error") el.classList.add("is-error");
    el.textContent = text;
  }

  async function saveForm(form, { reloadOnOk = false } = {}) {
    if (!(form instanceof HTMLFormElement)) return false;
    syncStudioFileserve(form);
    setSaveHint(form, "saving", "Saving…");
    const fd = new FormData(form);
    try {
      const res = await fetch(formUrl(form), {
        method: "POST",
        body: fd,
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        setSaveHint(form, "error", data.error || "Could not save");
        showFlash("error", data.error || "Could not save");
        return false;
      }
      setSaveHint(form, "ok", "Saves automatically");
      if (reloadOnOk) {
        window.location.reload();
        return true;
      }
      refreshListRow(form);
      return true;
    } catch {
      setSaveHint(form, "error", "Could not save");
      showFlash("error", "Could not save");
      return false;
    }
  }

  function refreshListRow(form) {
    const id = form.getAttribute("data-user-form");
    const row = shell.querySelector(`[data-user-select="${id}"]`);
    if (!row) return;
    const admin = form.querySelector('input[name="is_admin"][value="1"]');
    const isAdmin = admin instanceof HTMLInputElement && admin.checked;
    const role = row.querySelector(".users-col-role");
    const apps = row.querySelector(".users-col-apps");
    const sub = row.querySelector(".users-row-sub");
    const badge = row.querySelector(".users-row-badge");
    const countEl = form.closest("[data-user-detail]")?.querySelector("[data-grant-count]");
    const n = Number(countEl?.textContent || "0");
    const appsLabel = isAdmin ? "All apps" : n === 1 ? "1 app" : `${n} apps`;
    if (role) {
      role.innerHTML = isAdmin
        ? '<span class="chip chip-role">Admin</span>'
        : '<span class="chip chip-quiet">User</span>';
    }
    if (apps) apps.textContent = appsLabel;
    if (sub) sub.textContent = appsLabel;
    if (isAdmin && !badge) {
      const title = row.querySelector(".users-row-title");
      if (title) {
        const el = document.createElement("span");
        el.className = "chip chip-role users-row-badge";
        el.textContent = "Admin";
        title.appendChild(el);
      }
    } else if (!isAdmin && badge) {
      badge.remove();
    }
  }

  function closeMoreMenus() {
    shell.querySelectorAll("[data-users-more]").forEach((wrap) => {
      const menu = wrap.querySelector(".users-more-menu");
      const btn = wrap.querySelector("[data-users-more-toggle]");
      if (menu) menu.hidden = true;
      if (btn instanceof HTMLElement) {
        btn.setAttribute("aria-expanded", "false");
        btn.blur();
      }
    });
  }

  function activeForm() {
    return shell.querySelector(`[data-user-form="${selectedId}"]`);
  }

  shell.querySelectorAll("[data-user-select]").forEach((row) => {
    row.addEventListener("click", () => {
      selectUser(row.getAttribute("data-user-select") || "", { mobileDetail: true });
    });
  });

  shell.querySelectorAll("[data-users-back]").forEach((btn) => {
    btn.addEventListener("click", () => setView("list"));
  });

  document.querySelectorAll("[data-open-add-user]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      openSheet(addSheet);
      const first = addSheet?.querySelector('input[name="display_name"], input[name="username"]');
      if (first instanceof HTMLElement) window.setTimeout(() => first.focus(), 40);
    });
  });

  document.querySelectorAll("[data-close-add-user]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      closeSheet(addSheet);
    });
  });

  addSheet?.addEventListener("click", (event) => {
    if (event.target === addSheet) closeSheet(addSheet);
  });

  document.querySelectorAll("[data-close-reset-password]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      closeSheet(resetSheet);
    });
  });

  resetSheet?.addEventListener("click", (event) => {
    if (event.target === resetSheet) closeSheet(resetSheet);
  });

  document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const wrap = btn.closest(".users-password-wrap");
      const input = wrap?.querySelector("[data-password-input]");
      if (!(input instanceof HTMLInputElement)) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  });

  shell.querySelectorAll("[data-reset-password]").forEach((btn) => {
    btn.addEventListener("click", () => {
      resetUserId = btn.getAttribute("data-reset-password") || "";
      const detail = shell.querySelector(`[data-user-detail="${resetUserId}"]`);
      const name = detail?.querySelector("h2")?.textContent?.trim() || "this account";
      const label = resetSheet?.querySelector("[data-reset-password-for]");
      if (label) label.textContent = `Set a new password for ${name}.`;
      if (resetForm instanceof HTMLFormElement) {
        resetForm.action = `${shell.dataset.formBase || "/users"}/${resetUserId}`;
        const pw = resetForm.querySelector('input[name="password"]');
        if (pw instanceof HTMLInputElement) {
          pw.value = "";
          pw.type = "password";
        }
      }
      openSheet(resetSheet);
    });
  });

  resetForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = activeForm() || shell.querySelector(`[data-user-form="${resetUserId}"]`);
    if (!(form instanceof HTMLFormElement) || !(resetForm instanceof HTMLFormElement)) return;
    const pw = resetForm.querySelector('input[name="password"]');
    if (!(pw instanceof HTMLInputElement) || pw.value.length < 8) {
      showFlash("error", "Password must be at least 8 characters.");
      return;
    }
    const fd = new FormData(form);
    fd.set("password", pw.value);
    fd.set("action", "save");
    try {
      const res = await fetch(formUrl(form), {
        method: "POST",
        body: fd,
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        showFlash("error", data.error || "Could not update password.");
        return;
      }
      closeSheet(resetSheet);
      showFlash("ok", data.message || "Password updated");
    } catch {
      showFlash("error", "Could not update password.");
    }
  });

  shell.querySelectorAll("[data-users-more-toggle]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      const wrap = btn.closest("[data-users-more]");
      const menu = wrap?.querySelector(".users-more-menu");
      const open = menu && menu.hidden;
      closeMoreMenus();
      if (menu && open) {
        menu.hidden = false;
        btn.setAttribute("aria-expanded", "true");
      }
    });
  });

  document.addEventListener("click", () => closeMoreMenus());

  shell.querySelectorAll("[data-toggle-enabled]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-toggle-enabled") || "";
      const form = shell.querySelector(`[data-user-form="${id}"]`);
      const field = form?.querySelector("[data-enabled-field]");
      if (!(form instanceof HTMLFormElement) || !(field instanceof HTMLInputElement)) return;
      const currentlyOn = field.value === "1";
      const ok = await askConfirm({
        title: currentlyOn ? "Disable account?" : "Enable account?",
        body: currentlyOn
          ? "They won’t be able to sign in until you enable the account again."
          : "They will be able to sign in with their password.",
        okLabel: currentlyOn ? "Disable" : "Enable",
      });
      if (!ok) return;
      field.value = currentlyOn ? "0" : "1";
      const saved = await saveForm(form, { reloadOnOk: true });
      if (!saved) field.value = currentlyOn ? "1" : "0";
    });
  });

  shell.querySelectorAll("[data-delete-user]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-delete-user") || "";
      const username = btn.getAttribute("data-username") || "this account";
      const ok = await askConfirm({
        title: `Delete ${username}?`,
        body: "This cannot be undone.",
        okLabel: "Delete",
      });
      if (!ok) return;
      const form = shell.querySelector(`[data-user-form="${id}"]`);
      if (!(form instanceof HTMLFormElement)) return;
      const fd = new FormData(form);
      fd.set("action", "delete");
      try {
        const res = await fetch(formUrl(form), {
          method: "POST",
          body: fd,
          headers: { Accept: "application/json", "X-Requested-With": "fetch" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          showFlash("error", data.error || "Could not delete account.");
          return;
        }
        window.location.href = "/users?msg=Saved";
      } catch {
        showFlash("error", "Could not delete account.");
      }
    });
  });

  shell.querySelectorAll("[data-user-form]").forEach((form) => {
    if (!(form instanceof HTMLFormElement)) return;
    syncAdminLock(form);
    syncStudioFileserve(form);
    form.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      if (target.matches('input[name="is_admin"]')) syncAdminLock(form);
      if (target.matches('input[name="apps"]')) {
        syncCapsVisibility(form);
        updateGrantCount(form);
      }
      if (target.matches("[data-studio-can-publish]")) syncStudioFileserve(form);
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(() => saveForm(form), 280);
    });
  });

  const createForm = document.querySelector("[data-create-form]");
  if (createForm instanceof HTMLFormElement) {
    createForm.addEventListener("change", (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.matches('input[name="is_admin"]')) {
        syncAdminLock(createForm);
      }
      if (target instanceof HTMLInputElement && target.matches("[data-studio-can-publish]")) {
        syncStudioFileserve(createForm);
      }
      if (target instanceof HTMLInputElement && target.matches('input[name="apps"]')) {
        syncCapsVisibility(createForm);
      }
    });
    syncAdminLock(createForm);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (addSheet?.classList.contains("is-open")) closeSheet(addSheet);
    if (resetSheet?.classList.contains("is-open")) closeSheet(resetSheet);
  });

  if (selectedId) selectUser(selectedId, { mobileDetail: false });
  setView("list");
})();

// Success banners fade out on their own; errors stay until the next page.
function autoDismissFlash(el) {
  if (!(el instanceof HTMLElement) || !el.classList.contains("flash-ok")) return;
  window.setTimeout(() => {
    el.classList.add("is-leaving");
    window.setTimeout(() => el.remove(), 300);
  }, 4000);
}

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
  autoDismissFlash(el);
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
        const res = await fetch(formUrl(form), {
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
  // Read-only while Auth restarts: Home can't load the saved order, so don't save over it.
  if (!form || form.hasAttribute("data-launcher-readonly")) return;
  const grid = form.querySelector("[data-launcher-grid]");
  const orderInput = form.querySelector("[data-launcher-order]");
  const toggle = document.querySelector("[data-launcher-edit-toggle]");
  const lede = document.querySelector("[data-launcher-lede]");
  const editBar = form.querySelector("[data-launcher-edit-bar]");
  const cancel = form.querySelector("[data-launcher-cancel]");
  const status = form.querySelector("[data-launcher-status]");
  const statusDefault = status ? status.textContent : "";
  const ledeDefault = lede ? lede.textContent : "";
  let editing = false;
  // One save in flight at a time; a drop made meanwhile sets `pending` and the
  // latest order is sent when the current save finishes (never dropped).
  let saving = false;
  let pending = false;
  // Last order the server confirmed — restored on screen if a save fails.
  let savedIds = (orderInput?.value || "").split(",").filter(Boolean);
  let statusTimer = 0;
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

  function setStatus(text, kind) {
    if (!status) return;
    window.clearTimeout(statusTimer);
    status.textContent = text;
    status.classList.toggle("is-error", kind === "error");
    status.classList.toggle("is-ok", kind === "ok");
    if (kind === "ok") {
      statusTimer = window.setTimeout(() => {
        status.textContent = statusDefault;
        status.classList.remove("is-ok");
      }, 2500);
    }
  }

  function restoreOrder(ids) {
    const byId = new Map(tiles().map((tile) => [tile.dataset.appId, tile]));
    ids.forEach((id) => {
      const tile = byId.get(id);
      if (tile) grid.appendChild(tile);
    });
    syncOrderField();
    syncChrome();
  }

  function sameOrder(a, b) {
    return a.length === b.length && a.every((id, i) => id === b[i]);
  }

  async function persist() {
    window.clearTimeout(persistTimer);
    persistTimer = 0;
    const ids = syncOrderField();
    if (!ids.length) return;
    if (saving) {
      pending = true;
      return;
    }
    if (sameOrder(ids, savedIds)) {
      if (!editing) setStatus(statusDefault);
      return;
    }
    saving = true;
    pending = false;
    setStatus("Saving…");
    const fd = new FormData(form);
    fd.set("order", ids.join(","));
    let error = "";
    let loginUrl = "";
    try {
      const res = await fetch(formUrl(form), {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        savedIds = ids;
      } else {
        error = data.error || "Dashboard did not confirm the save";
        loginUrl = data.login_url || "";
      }
    } catch {
      error = "StonePi could not be reached";
    } finally {
      saving = false;
    }
    if (error) {
      // Never leave an unsaved order on screen: put back what the server has.
      pending = false;
      restoreOrder(savedIds);
      setStatus("Not saved", "error");
      const reason = error.replace(/[.\s]+$/, "");
      showFlash("error", "App order not saved — " + reason + ". Your previous order is back; try again.");
      if (loginUrl) window.setTimeout(() => window.location.assign(loginUrl), 1500);
      return;
    }
    if (pending) {
      pending = false;
      persist();
      return;
    }
    if (editing) setStatus("Saved", "ok");
    else showFlash("ok", "App order saved");
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

  // Leaving edit mode flushes a drop that's still waiting on its debounce; the
  // save carries on and reports through a flash once the edit bar is gone.
  function finishEditing() {
    if (persistTimer) persist();
    setEditing(false);
  }

  toggle?.addEventListener("click", () => (editing ? finishEditing() : setEditing(true)));
  cancel?.addEventListener("click", finishEditing);
  document.addEventListener("keydown", (event) => {
    if (editing && event.key === "Escape") finishEditing();
  });
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

  async function fetchStatus({ fresh = false } = {}) {
    const url = fresh ? "/api/network/status?fresh=1" : "/api/network/status";
    const res = await fetch(url, {
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
      const access = panel.querySelector("[data-ts-access]");
      const dns = panel.querySelector("[data-ts-dns]");
      const dnsDot = panel.querySelector("[data-ts-dns-dot]");
      const ipv4 = panel.querySelector("[data-ts-ipv4]");
      if (access && ts.access_url) {
        access.innerHTML =
          '<a class="network-auth-link" href="' +
          ts.access_url +
          '" target="_blank" rel="noopener noreferrer">' +
          ts.access_url +
          "</a>";
      } else if (access) {
        access.textContent = "—";
      }
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
      if (ipv4 && ts.ipv4) {
        ipv4.innerHTML =
          '<a class="network-auth-link" href="http://' +
          ts.ipv4 +
          '/" target="_blank" rel="noopener noreferrer"><code>http://' +
          ts.ipv4 +
          "/</code></a>";
      }
      const serveRow = panel.querySelector("[data-ts-serve-enable-row]");
      const serveEl = panel.querySelector("[data-ts-serve-enable]");
      if (!ts.serve_http && ts.serve_enable_url) {
        if (serveEl) {
          serveEl.innerHTML =
            '<a class="network-auth-link" href="' +
            ts.serve_enable_url +
            '" target="_blank" rel="noopener noreferrer">Enable HTTPS for this tailnet</a>';
        } else if (serveRow) {
          serveRow.hidden = false;
        }
      } else if (serveRow) {
        serveRow.hidden = true;
      }
      if (!panel.querySelector("[data-ts-disconnect-form]")) {
        window.location.reload();
      }
    } else if (ts.wanted && (ts.needs_login || ts.auth_url || connecting)) {
      startPoll();
    }
  }

  // Each fresh poll runs the Tailscale CLI on the Pi: every 4 s while you
  // finish signing in, and give up after 5 minutes (reload to check again).
  function startPoll() {
    if (pollTimer) return;
    const startedAt = Date.now();
    pollTimer = setInterval(() => {
      if (Date.now() - startedAt > 5 * 60 * 1000) {
        clearInterval(pollTimer);
        pollTimer = null;
        return;
      }
      if (document.hidden) return;
      fetchStatus({ fresh: true }).then(applyStatus).catch(() => {});
    }, 4000);
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

  function tabHasPanels(tab) {
    return (panelsByTab[tab] || []).length > 1;
  }

  function updateBack(mode, tab) {
    if (!settingsBack) return;
    if (mode === "hub") {
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
    const filterCards = panels.length > 1;
    settingsShell.querySelectorAll("[data-settings-panel-card]").forEach((card) => {
      const inTab = card.closest("[data-settings-panel]")?.dataset.settingsPanel === tab;
      const hide = filterCards && inTab && cards.size > 0 && !cards.has(card.dataset.settingsPanelCard);
      card.classList.toggle("is-panel-hidden", hide);
    });
    settingsShell.dataset.settingsActivePanel = active?.id || "";
    return active?.id || null;
  }

  function setMode({ hub = false, panelListMode = false } = {}) {
    const onHub = Boolean(hub);
    const onList = Boolean(panelListMode) && !onHub;
    settingsShell.dataset.settingsHub = onHub ? "true" : "false";
    settingsShell.dataset.settingsPanelList = onList ? "true" : "false";
    settingsShell.classList.toggle("is-hub", onHub);
    settingsShell.classList.toggle("is-panel-list", onList);
    if (hubList) hubList.hidden = !onHub;
    if (panelList) panelList.hidden = !onList;
    if (sectionEl) sectionEl.hidden = onHub || onList;
  }

  function showSettings(tab, { hub = false, panel = null, panelListMode = false } = {}) {
    const next = labelsByTab[tab] ? tab : "general";
    const showHub = Boolean(hub);
    const showList = Boolean(panelListMode) && !showHub && tabHasPanels(next);
    setMode({ hub: showHub, panelListMode: showList });
    settingsShell.dataset.settingsActiveTab = next;

    if (showHub) {
      if (settingsTitle) settingsTitle.textContent = "Settings";
      if (settingsLede) settingsLede.textContent = hubLede;
      updateBack("hub", next);
    } else if (showList) {
      renderPanelList(next);
      if (settingsTitle) settingsTitle.textContent = labelsByTab[next] || next;
      if (settingsLede) settingsLede.textContent = "Choose a section.";
      updateBack("panelList", next);
    } else {
      const panels = panelsByTab[next] || [];
      const active = panels.find((p) => p.id === panel) || panels[0];
      if (settingsTitle) {
        settingsTitle.textContent =
          active?.label || labelsByTab[next] || next;
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

  const initialHub = settingsShell.dataset.settingsHub === "true";
  const url = new URL(window.location.href);
  const urlPanel = url.searchParams.get("panel");
  const hash = (window.location.hash || "").replace(/^#/, "");
  const hashPanel =
    hash === "account-password" || hash === "view-options" || hash === "appearance" || hash === "app-colours" || hash === "remote-access"
      ? { "account-password": "password", "view-options": "view", appearance: "appearance", "app-colours": "app-colours", "remote-access": "remote" }[hash]
      : null;

  if (initialHub) {
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
      if (tabHasPanels(tab)) {
        const panels = panelsByTab[tab] || [];
        const active = panels.find((p) => p.id === panel) || panels[0];
        if (settingsTitle && active?.label) settingsTitle.textContent = active.label;
      }
    }
  }
}

/* "Auth is restarting — retrying…": reload with backoff until Auth answers. */
(function authWaitRetry() {
  const box = document.querySelector("[data-auth-wait]");
  if (!box) return;
  const target = box.getAttribute("data-retry-url") || window.location.pathname;
  const eta = box.querySelector("[data-auth-wait-eta]");
  const KEY = "stonepi-auth-wait";
  let attempt = 0;
  try {
    const prev = JSON.parse(sessionStorage.getItem(KEY) || "null");
    // Same page within the last minute: keep backing off; otherwise start fresh.
    if (prev && prev.url === target && Date.now() - prev.at < 60000) attempt = prev.n + 1;
    sessionStorage.setItem(KEY, JSON.stringify({ url: target, n: attempt, at: Date.now() }));
  } catch {
    /* private mode: fixed delay */
  }
  const delay = Math.min(3000 * Math.pow(1.5, attempt), 15000);
  let left = Math.ceil(delay / 1000);
  const tick = () => {
    if (eta) eta.textContent = " in " + left + " s";
    left -= 1;
  };
  tick();
  const timer = window.setInterval(tick, 1000);
  window.setTimeout(() => {
    window.clearInterval(timer);
    window.location.replace(target);
  }, delay);
})();

/* Health page: keep the banner and per-app states current without a reload.
   Polls every 3 s while anything is starting/down, eases off when nothing changes,
   20 s once all clear; pauses while the tab is hidden. */
(function healthPoller() {
  const live = document.querySelector("[data-health-live]");
  if (!live) return;
  const url = live.getAttribute("data-health-poll-url") || "/api/overview/watch";
  const FAST = 3000;
  const SLOW = 20000;
  let delay = live.getAttribute("data-health-settled") === "true" ? SLOW : FAST;
  let timer = 0;
  let lastPrint = "";
  let inFlight = null;
  let stopped = false;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderBanner(data) {
    live.replaceChildren();
    if (!data.ready) {
      const aside = el("aside", "overview-alert is-attention");
      aside.setAttribute("role", "status");
      const body = el("div");
      body.append(el("p", "overview-alert-level", "checking…"), el("p", "overview-alert-title", "Gathering host and app health"));
      aside.append(body);
      live.append(aside);
      return;
    }
    if (data.level === "healthy") return;
    const aside = el("aside", "overview-alert is-" + data.level);
    aside.setAttribute("role", "status");
    const body = el("div");
    body.append(el("p", "overview-alert-level", data.level), el("p", "overview-alert-title", data.summary || ""));
    if (data.summary_detail) body.append(el("p", "overview-alert-detail", data.summary_detail));
    aside.append(body);
    if (data.checked_at) {
      aside.append(el("p", "overview-alert-checked", "Checked " + String(data.checked_at).slice(0, 19).replace("T", " ") + " UTC"));
    }
    live.append(aside);
  }

  function stateLabel(card) {
    if (!card.enabled) return ["Disabled", "off", "is-off"];
    if (card.health_ok) return ["Healthy", "ok", "is-ok"];
    const unit = card.unit_status || "";
    if (unit === "activating" || unit === "reloading") return ["Starting", "warn", "is-bad"];
    if (unit === "deactivating") return ["Stopping", "warn", "is-bad"];
    return ["Down", "bad", "is-bad"];
  }

  function renderCards(data) {
    (data.cards || []).forEach((card) => {
      const node = document.querySelector('[data-health-card="' + CSS.escape(card.id) + '"]');
      if (!node) return;
      const [label, dot, statusCls] = stateLabel(card);
      node.classList.toggle("is-disabled", !card.enabled);
      node.classList.toggle("is-unhealthy", card.enabled && !card.health_ok);
      const status = node.querySelector("[data-health-status]");
      if (status) {
        status.classList.remove("is-off", "is-ok", "is-bad");
        status.classList.add(statusCls);
      }
      const dotEl = node.querySelector("[data-health-dot]");
      if (dotEl) dotEl.className = "dot " + dot;
      const labelEl = node.querySelector("[data-health-label]");
      if (labelEl) labelEl.textContent = label;
      const unitEl = node.querySelector("[data-health-unit]");
      if (unitEl && card.unit_status) unitEl.textContent = card.unit_status === "local" ? "local process" : card.unit_status;
    });
    const stat = document.querySelector('[data-health-count="stat"]');
    if (stat) stat.textContent = data.healthy_count + " / " + data.total;
    const head = document.querySelector('[data-health-count="head"]');
    if (head) head.textContent = data.healthy_count + " of " + data.total + " responding";
  }

  function schedule(ms) {
    window.clearTimeout(timer);
    if (stopped || document.hidden) return;
    timer = window.setTimeout(poll, ms);
  }

  async function poll() {
    if (stopped || document.hidden) return;
    inFlight = new AbortController();
    try {
      const res = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
        signal: inFlight.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401 && data.login_url) {
        stopped = true;
        window.location.assign(data.login_url);
        return;
      }
      if (!res.ok || !data.ok) throw new Error("poll failed");
      // Fingerprint everything we paint (not checked_at): unchanged + unsettled eases off to 20 s.
      const print = JSON.stringify([data.ready, data.level, data.summary, data.summary_detail, data.cards]);
      const changed = print !== lastPrint;
      lastPrint = print;
      renderBanner(data);
      renderCards(data);
      if (data.settled) delay = SLOW;
      else if (changed || data.transitional) delay = FAST;
      else delay = Math.min(SLOW, Math.round(delay * 1.5));
    } catch (err) {
      if (err && err.name === "AbortError") return;
      // Dashboard itself restarting, or a blip: try again a little later.
      delay = Math.min(SLOW, Math.max(FAST, delay * 2));
    } finally {
      inFlight = null;
    }
    schedule(delay);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
    } else {
      // Back on the tab: catch up straight away.
      delay = FAST;
      schedule(0);
    }
  });
  window.addEventListener("pagehide", () => {
    stopped = true;
    window.clearTimeout(timer);
    if (inFlight) inFlight.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted && stopped) {
      stopped = false;
      delay = FAST;
      schedule(0);
    }
  });
  schedule(delay === SLOW ? SLOW : FAST);
})();
