(function () {
  const THEME_KEY = "stonepi-theme";
  const PALETTE_KEY = "stonepi-palette";
  const LEGACY_THEME = ["newscast-theme", "fileserve-theme", "eventtrakr-theme"];
  const LEGACY_PALETTE = ["newscast-palette", "fileserve-palette", "eventtrakr-palette"];
  const PALETTES = ["default", "ocean", "forest", "slate"];
  const THEME_COLORS = {
    default: { light: "#f3eee4", dark: "#12100d" },
    ocean: { light: "#e7eef5", dark: "#0c141c" },
    forest: { light: "#eef1e6", dark: "#10140d" },
    slate: { light: "#ececee", dark: "#121314" },
  };

  function readShared(shared, legacy, fallback) {
    let value = localStorage.getItem(shared);
    if (value) return value;
    for (const key of legacy) {
      value = localStorage.getItem(key);
      if (value) {
        localStorage.setItem(shared, value);
        return value;
      }
    }
    return fallback;
  }

  function resolvedTheme(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function savedPalette() {
    const value = readShared(PALETTE_KEY, LEGACY_PALETTE, "default");
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
  }

  applyTheme(readShared(THEME_KEY, LEGACY_THEME, "system"));
  document.querySelectorAll("[data-theme-set]").forEach((button) => {
    button.addEventListener("click", () => {
      localStorage.setItem(THEME_KEY, button.dataset.themeSet);
      applyTheme(button.dataset.themeSet);
    });
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((localStorage.getItem(THEME_KEY) || "system") === "system") applyTheme("system");
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
    if (pass) pass.required = on;
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
        if (typeof form.requestSubmit === "function") form.requestSubmit(submitter || undefined);
        else form.submit();
      });
    });
  });

  const form = document.querySelector("[data-chat-form]");
  if (!form) return;
  const thread = document.querySelector("[data-chat-thread]");
  const fileList = document.querySelector("[data-file-list]");
  const preview = document.querySelector("[data-preview]") || document.querySelector(".preview");
  const previewStatus = document.querySelector("[data-preview-status]");
  const split = document.querySelector("[data-studio-split]");
  const workflowHint = document.querySelector("[data-workflow-hint]");
  const workflowActions = document.querySelector("[data-workflow-actions]");
  const describeActions = document.querySelector("[data-describe-actions]");
  const intentField = form.querySelector("[data-intent-field]");
  const chatInput = form.querySelector("[data-chat-input]");
  const projectId = form.getAttribute("data-project-id");
  const chatUrl = window.location.pathname.replace(/\/?$/, "") + "/chat";
  let busy = false;

  function previewBase() {
    if (!preview) return "";
    const raw = preview.getAttribute("data-preview-base") || preview.src || "";
    return raw.split("?")[0];
  }

  if (preview) {
    preview.setAttribute("data-preview-base", previewBase());
  }

  function reloadPreview() {
    if (!preview) return;
    const base = previewBase() || preview.src.split("?")[0];
    preview.src = base + (base.includes("?") ? "&" : "?") + "t=" + Date.now();
  }

  function setBuiltUi(built) {
    form.dataset.hasBuilt = built ? "1" : form.dataset.hasBuilt || "";
    if (built) {
      split?.classList.add("is-built");
      split?.setAttribute("data-built", "1");
      if (previewStatus) previewStatus.textContent = "Live from your last build";
      if (workflowHint) {
        workflowHint.textContent =
          "Preview is live on the right. Clarify further, then rebuild when you want changes — publish only when you’re happy.";
      }
      const buildLabel = form.querySelector("[data-build-label]");
      if (buildLabel) buildLabel.textContent = "Rebuild";
      if (chatInput) chatInput.placeholder = "Clarify changes for the next build…";
      preview?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function setDescribedUi() {
    form.dataset.hasDescribed = "1";
    if (describeActions) describeActions.hidden = true;
    if (workflowActions) workflowActions.hidden = false;
    if (chatInput) {
      chatInput.required = false;
      chatInput.placeholder = form.dataset.hasBuilt
        ? "Clarify changes for the next build…"
        : "Add detail, or leave blank and press Build…";
    }
    if (workflowHint && !form.dataset.hasBuilt) {
      workflowHint.textContent = "Add clarification if needed, then Build to generate the preview.";
    }
  }

  form.querySelectorAll("[data-intent]").forEach((button) => {
    button.addEventListener("click", () => {
      if (intentField) intentField.value = button.getAttribute("data-intent") || "clarify";
    });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy || !projectId) return;
    const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
    const intent =
      (submitter && submitter.getAttribute("data-intent")) ||
      (intentField && intentField.value) ||
      "clarify";
    if (intentField) intentField.value = intent;
    const fd = new FormData(form);
    fd.set("intent", intent);
    let text = (fd.get("message") || "").toString().trim();
    if (!text && intent === "build") {
      text = "Build the site from our conversation so far.";
      fd.set("message", text);
    }
    if (!text) return;
    busy = true;
    form.querySelectorAll("button").forEach((b) => {
      b.disabled = true;
    });
    appendMsg("user", text);
    if (chatInput) chatInput.value = "";
    fetch(chatUrl, {
      method: "POST",
      body: fd,
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, body: j };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          appendMsg("assistant", res.body.message || "Request failed.");
          return;
        }
        appendMsg("assistant", res.body.message || "Done.");
        setDescribedUi();
        if (fileList && res.body.files) {
          fileList.innerHTML = res.body.files
            .map(function (f) {
              return "<li>" + escapeHtml(f) + "</li>";
            })
            .join("");
          const summary = form.closest(".chat-pane")?.querySelector(".files > summary");
          if (summary) summary.textContent = "Files (" + res.body.files.length + ")";
        }
        if (intent === "build" || res.body.built || (res.body.applied && res.body.applied.length)) {
          setBuiltUi(true);
          reloadPreview();
        }
      })
      .catch(function () {
        appendMsg("assistant", "Network error.");
      })
      .finally(function () {
        busy = false;
        form.querySelectorAll("button").forEach((b) => {
          b.disabled = false;
        });
      });
  });

  function appendMsg(role, content) {
    if (!thread) return;
    const empty = thread.querySelector(":scope > .hint");
    if (empty) empty.remove();
    const div = document.createElement("div");
    div.className = "msg msg-" + role;
    div.innerHTML = "<strong>" + role + ":</strong> " + escapeHtml(content);
    thread.appendChild(div);
    thread.scrollTop = thread.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
})();
