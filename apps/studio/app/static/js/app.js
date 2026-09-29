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
  }

  applyTheme(readShared(THEME_KEY, LEGACY_THEME, "system"));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (readShared(THEME_KEY, LEGACY_THEME, "system") === "system") applyTheme("system");
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

  // Settings → Model: expand the model card(s) for the chosen service, collapse the other.
  const providerChoices = document.querySelectorAll("[data-provider-choice]");
  providerChoices.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      document.querySelectorAll("[data-model-panel]").forEach((panel) => {
        panel.open = radio.value === "auto" || panel.dataset.modelPanel === radio.value;
      });
    });
  });
  // Keep each collapsed card's summary showing the model that is picked inside it.
  document.querySelectorAll("[data-model-panel]").forEach((panel) => {
    const current = panel.querySelector(".model-panel-current");
    panel.addEventListener("change", (event) => {
      const t = event.target;
      if (!current || !(t instanceof HTMLInputElement)) return;
      if (t.type === "radio" && t.checked) {
        current.textContent = t.value === "custom" ? panel.querySelector("input[type=text]")?.value || "Other model ID" : t.value;
      } else if (t.type === "text") {
        current.textContent = t.value || "Other model ID";
      }
    });
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

  document.querySelectorAll("button[type='submit'][data-confirm]").forEach((button) => {
    button.addEventListener("click", (event) => {
      const form = button.form;
      if (!form || form.dataset.confirmPassed === "1") return;
      event.preventDefault();
      askConfirm({
        title: button.dataset.confirm,
        body: button.dataset.confirmDetail || "",
        okLabel: button.dataset.confirmOk || "Continue",
      }).then((ok) => {
        if (!ok) return;
        form.dataset.confirmPassed = "1";
        if (typeof form.requestSubmit === "function") form.requestSubmit(button);
        else form.submit();
      });
    });
  });

  /* ---------- Rename project (editor header + Projects cards) ---------- */

  const renameSheet = document.querySelector("[data-rename-sheet]");
  if (renameSheet) {
    const renameForm = renameSheet.querySelector("[data-rename-form]");
    const renameField = renameSheet.querySelector("[data-rename-field]");
    const closeRename = () => {
      if (typeof renameSheet.close === "function") renameSheet.close();
      else renameSheet.removeAttribute("open");
    };
    document.querySelectorAll("[data-rename-open]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (renameForm && btn.dataset.renameAction) renameForm.action = btn.dataset.renameAction;
        if (renameField) renameField.value = btn.dataset.renameName || "";
        if (typeof renameSheet.showModal === "function") renameSheet.showModal();
        else renameSheet.setAttribute("open", "");
        renameField?.focus();
        renameField?.select();
      });
    });
    renameSheet.querySelector("[data-rename-cancel]")?.addEventListener("click", closeRename);
    renameSheet.addEventListener("click", (event) => {
      if (event.target === renameSheet) closeRename();
    });
    renameForm?.addEventListener("submit", (event) => {
      if (renameField && !renameField.value.trim()) {
        event.preventDefault();
        renameField.value = "";
        renameField.reportValidity();
      }
    });
  }

  const thread = document.querySelector("[data-chat-thread]");
  const form = document.querySelector("[data-chat-form]");
  const preview = document.querySelector("[data-preview]");
  const previewStage = document.querySelector("[data-preview-stage]");
  const previewStatus = document.querySelector("[data-preview-status]");
  const buddyEl = document.querySelector("[data-buddy]");
  const issuesBox = document.querySelector("[data-preview-issues]");
  const issuesList = document.querySelector("[data-issues-list]");
  const issuesTitle = document.querySelector("[data-issues-title]");
  const fixBtn = document.querySelector("[data-fix-issues]");
  const publishForm = document.querySelector("[data-publish-form]");
  const fileList = document.querySelector("[data-file-list]");
  const split = document.querySelector("[data-studio-split]");
  let busy = false;
  let controller = null;

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Tiny, safe Markdown: paragraphs, bullet/numbered lists, **bold**, *italic*, `code`.
  function renderMd(src) {
    const inline = (t) =>
      escapeHtml(t)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    const out = [];
    let list = null;
    const flush = () => {
      if (list) out.push("</" + list + ">");
      list = null;
    };
    String(src || "")
      .replace(/\r\n/g, "\n")
      .split(/\n{2,}/)
      .forEach((block) => {
        const lines = block.split("\n");
        const bullets = lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l) || !l.trim());
        if (bullets && lines.some((l) => l.trim())) {
          const ordered = /^\s*\d/.test(lines.find((l) => l.trim()));
          const tag = ordered ? "ol" : "ul";
          if (list !== tag) {
            flush();
            out.push("<" + tag + ">");
            list = tag;
          }
          lines.forEach((l) => {
            if (l.trim()) out.push("<li>" + inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, "")) + "</li>");
          });
          return;
        }
        flush();
        const heading = block.match(/^#{1,4}\s+(.*)$/);
        if (heading) out.push("<p><strong>" + inline(heading[1]) + "</strong></p>");
        else out.push("<p>" + lines.map(inline).join("<br>") + "</p>");
      });
    flush();
    return out.join("");
  }

  function setMd(el, text) {
    el.innerHTML = renderMd(text);
    el.classList.add("is-md");
  }

  thread?.querySelectorAll("[data-md]").forEach((el) => setMd(el, el.textContent.trim()));

  function scrollThread() {
    if (thread) thread.scrollTop = thread.scrollHeight;
  }
  // A new project is just the assistant intro plus starter ideas. Desktop shows
  // that whole thread; phones cap it, and landing at the bottom hides the intro.
  function pinChatStart() {
    if (!thread) return;
    const opening = thread.querySelector(".msg-intro") && thread.querySelectorAll(".msg").length === 1;
    const mobile = window.matchMedia("(max-width: 1023px)").matches;
    if (!opening || !mobile) {
      scrollThread();
      return;
    }
    const pin = () => {
      thread.scrollTop = 0;
    };
    pin();
    requestAnimationFrame(pin);
  }
  pinChatStart();

  /* ---------- Preview ---------- */

  const SIZE_KEY = "studio-preview-size";
  const DEVICE = { phone: [402, 856], laptop: [1280, 800] };
  function fitDevice() {
    if (!previewStage) return;
    const dims = DEVICE[previewStage.dataset.size];
    if (!dims) return previewStage.style.removeProperty("--preview-scale");
    const scale = Math.min(1, (previewStage.clientWidth - 16) / dims[0], (previewStage.clientHeight - 16) / dims[1]);
    previewStage.style.setProperty("--preview-scale", Math.max(0.2, scale).toFixed(3));
  }
  if (previewStage && "ResizeObserver" in window) new ResizeObserver(fitDevice).observe(previewStage);
  function setSize(size) {
    if (!previewStage) return;
    previewStage.dataset.size = size;
    fitDevice();
    document.querySelectorAll("[data-preview-size]").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.previewSize === size);
    });
    try {
      localStorage.setItem(SIZE_KEY, size);
    } catch (_err) {
      /* private mode */
    }
  }
  document.querySelectorAll("[data-preview-size]").forEach((b) => {
    b.addEventListener("click", () => setSize(b.dataset.previewSize));
  });
  try {
    const saved = localStorage.getItem(SIZE_KEY);
    if (saved) setSize(saved);
  } catch (_err) {
    /* private mode */
  }

  const previewBase = preview ? (preview.getAttribute("data-href") || preview.getAttribute("src") || "").split("?")[0] : "";

  /* ---------- Pixel, the waiting buddy ---------- */

  const BUDDY_LINES = {
    idle: {
      game: [
        "Hi! I'm Pixel. Tell me your game idea in the chat and I'll build it right here!",
        "Who's the hero of your game? A cat? A dragon? A robot like me? 🤖",
        "Tip: tell me how you win and how you lose. That makes the best games!",
        "Finished describing? Press Build my game and I'll get to work!",
      ],
      guide: [
        "Hi! I'm Pixel. Tell me your story or guide idea and I'll build it right here!",
        "Adventures are fun when the reader gets to choose what happens next…",
        "Tip: tell me who it's for. I'll pick the right words for them.",
        "Finished describing? Press Build my story and I'll get to work!",
      ],
      spa: [
        "Hi! I'm Pixel. Tell me what your app should do and I'll build it right here!",
        "What should it remember? A list? Your scores? A countdown?",
        "Tip: the more you describe, the better it turns out!",
        "Finished describing? Press Build my app and I'll get to work!",
      ],
    },
    building: [
      "Ooh, great idea! Let me get building…",
      "I'm thinking about how it should work… 🤔",
      "This usually takes a minute or two. Not long now!",
      "Adding colours and sparkle ✨",
      "Still going! Big ideas take a little longer.",
      "Nearly there… I'm checking everything works.",
    ],
    rebuilding: [
      "On it! Let me make those changes…",
      "Tweaking things just how you asked 🔧",
      "Not long now, I'm nearly done!",
      "Checking it all still works…",
    ],
    files: {
      "index.html": "I'm building the skeleton, the page everything sits on!",
      "style.css": "Now the colours and style 🎨",
      "app.js": "Now the brains, the code that makes it all work! 🧠",
    },
    oops: "Hmm, that didn't quite work. Press the build button and let's try again! 💪",
  };

  const buddy = (() => {
    if (!buddyEl) return { show() {}, hide() {}, say() {}, progress() {} };
    const bubble = buddyEl.querySelector("[data-buddy-say]");
    const bar = buddyEl.querySelector("[data-buddy-progress]");
    const kind = buddyEl.dataset.kind || "spa";
    const name = (buddyEl.dataset.name || "").trim();
    const personal = (text) => (name ? text.replace(/^Hi!/, "Hi " + name + "!") : text);
    let timer = null;
    let lines = [];
    let idx = 0;
    let lockedUntil = 0;
    const say = (text) => {
      if (!bubble || bubble.textContent === personal(text)) return;
      bubble.classList.remove("is-new");
      void bubble.offsetWidth; // restart the pop-in animation
      bubble.textContent = personal(text);
      bubble.classList.add("is-new");
    };
    // Idle tips loop; build lines advance once and then hold on the last ("nearly there").
    const rotate = () => {
      if (Date.now() < lockedUntil || !lines.length) return;
      idx = buddyEl.dataset.state === "idle" ? (idx + 1) % lines.length : Math.min(idx + 1, lines.length - 1);
      say(lines[idx]);
    };
    return {
      show(state, overlay) {
        buddyEl.hidden = false;
        buddyEl.dataset.state = state;
        buddyEl.classList.toggle("is-overlay", Boolean(overlay));
        lines = state === "idle" ? BUDDY_LINES.idle[kind] || BUDDY_LINES.idle.spa : BUDDY_LINES[state] || [];
        idx = 0;
        lockedUntil = 0;
        if (lines.length) say(lines[0]);
        if (bar) bar.hidden = state === "idle";
        clearInterval(timer);
        timer = setInterval(rotate, state === "idle" ? 6000 : 5000);
      },
      hide() {
        clearInterval(timer);
        buddyEl.hidden = true;
        buddyEl.dataset.state = "off";
      },
      say,
      // Real progress (which file is being written) takes over from the canned lines for a bit.
      progress(file) {
        const line = BUDDY_LINES.files[file] || "Now I'm writing " + file + "…";
        lockedUntil = Date.now() + 7000;
        say(line);
      },
    };
  })();
  if (buddyEl && !buddyEl.hidden) buddy.show("idle");
  let runtimeErrors = [];
  let buildIssues = [];
  // Set by the chat code: called when the freshly built preview throws.
  let onRuntimeError = () => {};

  function setStatus(state, text) {
    if (!previewStatus) return;
    previewStatus.dataset.state = state;
    previewStatus.textContent = text;
  }

  function renderIssues() {
    if (!issuesBox || !issuesList) return;
    const all = buildIssues.concat(runtimeErrors);
    issuesBox.hidden = all.length === 0;
    issuesList.innerHTML = all.map((m) => "<li>" + escapeHtml(m) + "</li>").join("");
    if (issuesTitle) {
      issuesTitle.textContent =
        all.length === 1 ? "1 problem found in the preview" : all.length + " problems found in the preview";
    }
    if (all.length) setStatus("error", "Needs attention");
    else if (form?.dataset.hasBuilt) setStatus("live", "Live from your last build");
  }

  function reloadPreview() {
    if (!preview) return;
    runtimeErrors = [];
    renderIssues();
    preview.hidden = false;
    preview.src = previewBase + "?t=" + Date.now();
  }
  document.querySelector("[data-preview-reload]")?.addEventListener("click", reloadPreview);

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.source !== "studio-preview" || event.source !== preview?.contentWindow) return;
    if (data.type === "error") {
      const where = data.file ? " (" + data.file + (data.line ? ":" + data.line : "") + ")" : "";
      const line = data.message + where;
      if (!runtimeErrors.includes(line) && runtimeErrors.length < 8) runtimeErrors.push(line);
      renderIssues();
      onRuntimeError();
    } else if (data.type === "ready" && data.empty && form?.dataset.hasBuilt) {
      const msg = "The page loaded but shows nothing — the game/app may not have started.";
      if (!runtimeErrors.includes(msg)) runtimeErrors.push(msg);
      renderIssues();
    }
  });

  let focusPreviewOnLoad = false;
  preview?.addEventListener("load", () => {
    // Straight after a build: the wait is over, so swap Pixel out for the live preview and
    // hand the keyboard to the game so it's playable at once.
    if (focusPreviewOnLoad) {
      focusPreviewOnLoad = false;
      buddy.hide();
      preview.contentWindow?.focus();
    }
  });

  // Guardrail: confirm before publishing something the preview flagged.
  /* ---------- Share (publish) ---------- */

  const shareSheet = document.querySelector("[data-share-sheet]");
  const shareSubmit = document.querySelector("[data-share-submit]");
  const openSheet = (dlg) => {
    if (!dlg) return;
    if (typeof dlg.showModal === "function") dlg.showModal();
    else dlg.setAttribute("open", "");
  };
  const closeSheet = (dlg) => {
    if (!dlg) return;
    if (typeof dlg.close === "function") dlg.close();
    else dlg.removeAttribute("open");
  };
  document.querySelectorAll("[data-share-open]").forEach((btn) => {
    btn.addEventListener("click", () => {
      openSheet(shareSheet);
      shareSheet?.querySelector("input[name=title]")?.focus();
    });
  });
  document.querySelector("[data-share-cancel]")?.addEventListener("click", () => closeSheet(shareSheet));
  // Click on the dim backdrop closes the sheet.
  [shareSheet, document.querySelector("[data-share-party]")].forEach((dlg) => {
    dlg?.addEventListener("click", (event) => {
      if (event.target === dlg) closeSheet(dlg);
    });
  });

  const party = document.querySelector("[data-share-party]");
  if (party) {
    openSheet(party);
    document.querySelector("[data-party-close]")?.addEventListener("click", () => closeSheet(party));
    // Drop ?msg=Published so a refresh doesn't throw the party again.
    try {
      history.replaceState(null, "", window.location.pathname);
    } catch (_err) {
      /* ignore */
    }
  }

  publishForm?.addEventListener(
    "submit",
    (event) => {
      const count = buildIssues.length + runtimeErrors.length;
      if (!count || publishForm.dataset.issuesOk === "1") {
        delete publishForm.dataset.issuesOk;
        if (shareSubmit) {
          shareSubmit.disabled = true;
          shareSubmit.textContent = "Sharing… 🚀";
        }
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      closeSheet(shareSheet); // the confirm sheet can't sit above a modal dialog
      askConfirm({
        title: "Share it with " + count + (count === 1 ? " problem?" : " problems?"),
        body: "The preview found a bug. People at home will probably see it too. Fix it first?",
        okLabel: "Share anyway",
      }).then((ok) => {
        if (!ok) return;
        publishForm.dataset.issuesOk = "1";
        publishForm.requestSubmit();
      });
    },
    true
  );

  if (!form) return;

  /* ---------- Chat ---------- */

  const chatInput = form.querySelector("[data-chat-input]");
  const intentField = form.querySelector("[data-intent-field]");
  const buildBtn = document.querySelector("[data-build-btn]");
  const buildBar = document.querySelector("[data-build-bar]");
  let pendingChanges = Number(buildBar?.dataset.pending || 0);

  // The build bar is the "I've finished describing" step. It only appears when there is
  // something to build: a described idea before the first build, or new asks after one.
  function updateBuildBar() {
    if (!buildBar) return;
    const built = Boolean(form.dataset.hasBuilt);
    const noun = buildBar.dataset.noun || "app";
    const show = built ? pendingChanges > 0 : Boolean(form.dataset.hasDescribed);
    buildBar.hidden = !show;
    buildBar.classList.toggle("is-rebuild", built);
    const set = (sel, text) => {
      const el = buildBar.querySelector(sel);
      if (el) el.textContent = text;
    };
    if (built) {
      set("[data-build-bar-emoji]", "🔁");
      set("[data-build-bar-title]", "Ready to try your changes?");
      set(
        "[data-build-bar-sub]",
        "You've asked for " + pendingChanges + " change" + (pendingChanges === 1 ? "" : "s") + " since the last build."
      );
      set("[data-build-label]", "Rebuild with changes");
    } else {
      set("[data-build-bar-emoji]", "✨");
      set("[data-build-bar-title]", "Happy with the plan?");
      set("[data-build-bar-sub]", "When you've finished describing, build it and try it out.");
      set("[data-build-label]", "Build my " + noun);
    }
    const hint = form.querySelector(".composer-hint");
    if (hint) hint.textContent = "Enter to send · Shift+Enter for a new line" + (show ? " · Ctrl+Enter to build" : "");
    if (show && !buildBar.dataset.shown) {
      buildBar.dataset.shown = "1";
      buildBar.classList.remove("is-new");
      void buildBar.offsetWidth;
      buildBar.classList.add("is-new");
    }
    if (!show) delete buildBar.dataset.shown;
    // One call to action at a time: while changes wait to be built, sharing steps back.
    document.querySelector("[data-share-step]")?.classList.toggle("is-waiting", built && pendingChanges > 0);
  }
  const sendBtn = form.querySelector("[data-send-btn]");
  const stopBtn = form.querySelector("[data-stop]");
  const chatUrl = window.location.pathname.replace(/\/?$/, "") + "/chat";
  function autoGrow() {
    if (!chatInput) return;
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 192) + "px";
  }
  chatInput?.addEventListener("input", autoGrow);
  chatInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (busy) return;
    // Plain Enter chats; Ctrl/Cmd+Enter is the shortcut for the build bar's button.
    const wantsBuild = (event.ctrlKey || event.metaKey) && (!buildBar?.hidden || chatInput.value.trim());
    form.requestSubmit((wantsBuild ? buildBtn : sendBtn) || undefined);
  });

  thread?.querySelectorAll("[data-suggestion]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!chatInput) return;
      chatInput.value = chip.dataset.suggestionText || chip.textContent.trim();
      autoGrow();
      chatInput.focus();
    });
  });

  function setStep(name, state) {
    const li = document.querySelector('[data-step="' + name + '"]');
    if (!li) return;
    li.classList.remove("is-done", "is-current");
    if (state) li.classList.add(state);
  }

  function markDescribed() {
    form.dataset.hasDescribed = "1";
    setStep("describe", "is-done");
    if (!form.dataset.hasBuilt) setStep("build", "is-current");
    updateBuildBar();
  }

  function markBuilt() {
    form.dataset.hasBuilt = "1";
    split?.classList.add("is-built");
    document.querySelectorAll("[data-publish-area], [data-preview-open], [data-preview-foot]").forEach((el) => {
      el.hidden = false;
    });
    setStep("publish", "");
    setStep("build", "is-done");
    setStep("check", "is-current");
    pendingChanges = 0;
    updateBuildBar();
    if (chatInput) chatInput.placeholder = "Ask for a change, e.g. “make the enemies slower”…";
  }

  function addMsg(role, html, extraClass) {
    const div = document.createElement("div");
    div.className = "msg msg-" + role + (extraClass ? " " + extraClass : "");
    div.innerHTML = html;
    thread?.appendChild(div);
    scrollThread();
    return div;
  }

  const CLARIFY_LINES = ["Message received", "Reading your idea", "Thinking it through", "Writing a reply"];
  const BUILD_LINES = [
    "Message received",
    "Reading the conversation",
    "Planning the build",
    "Designing the layout",
    "Wiring up the logic",
  ];

  function fmtElapsed(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function fmtSize(chars) {
    return chars < 1024 ? chars + " chars" : (chars / 1024).toFixed(1) + " KB";
  }

  function setBusy(on, intent) {
    busy = on;
    form.classList.toggle("is-busy", on);
    if (buildBtn) buildBtn.disabled = on;
    if (sendBtn) sendBtn.disabled = on;
    if (stopBtn) stopBtn.hidden = !on;
    if (buildBar) {
      if (on) buildBar.hidden = true;
      else updateBuildBar();
    }
    if (on && intent === "build") {
      const rebuilding = Boolean(form.dataset.hasBuilt);
      buddy.show(rebuilding ? "rebuilding" : "building", rebuilding);
      setStatus("busy", rebuilding ? "Rebuilding…" : "Building…");
    }
  }

  function pendingBubble(intent) {
    const el = addMsg(
      "assistant",
      '<div class="msg-status"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>' +
        '<span data-status-text>Message received…</span><span class="elapsed" data-elapsed>0:00</span></div>' +
        '<div class="msg-body is-md" data-live-body></div><ul class="msg-files" data-live-files></ul>',
      "is-pending"
    );
    const statusText = el.querySelector("[data-status-text]");
    const elapsed = el.querySelector("[data-elapsed]");
    const lines = intent === "build" ? BUILD_LINES : CLARIFY_LINES;
    const started = Date.now();
    let idx = 0;
    let locked = false; // real file progress beats the canned lines
    const setText = (t) => {
      if (statusText) statusText.textContent = t;
    };
    const tick = setInterval(() => {
      if (elapsed) elapsed.textContent = fmtElapsed(Date.now() - started);
      const due = Math.min(lines.length - 1, Math.floor((Date.now() - started) / 4500));
      if (!locked && due > idx) {
        idx = due;
        setText(lines[idx] + "…");
      }
    }, 500);
    return {
      el,
      isAutoFix: false,
      body: el.querySelector("[data-live-body]"),
      files: el.querySelector("[data-live-files]"),
      status(text, lock) {
        if (lock) locked = true;
        setText(text);
      },
      finish() {
        clearInterval(tick);
        el.classList.remove("is-pending");
        el.querySelector(".msg-status")?.remove();
      },
    };
  }

  function renderFiles(bubble, ev) {
    if (!bubble.files) return;
    const rows = (ev.done || []).map((f) => '<li class="is-done">' + escapeHtml(f) + "</li>");
    if (ev.writing) {
      rows.push('<li class="is-writing">' + escapeHtml(ev.writing) + " · " + fmtSize(ev.chars || 0) + "</li>");
      if (bubble.lastFile !== ev.writing) {
        bubble.lastFile = ev.writing;
        buddy.progress(ev.writing);
      }
      bubble.status("Writing " + ev.writing + "…", true);
    }
    bubble.files.innerHTML = rows.join("");
    scrollThread();
  }

  function finishBuild(bubble, ev) {
    if (ev.note) {
      const note = document.createElement("p");
      note.className = "msg-note";
      note.textContent = ev.note;
      bubble.el.appendChild(note);
    }
    bubble.files?.remove();
    if (fileList && ev.files) {
      fileList.innerHTML = ev.files.map((f) => "<li>" + escapeHtml(f) + "</li>").join("");
      const summary = document.querySelector(".files > summary");
      if (summary) {
        summary.textContent = "Behind the scenes · " + ev.files.length + " file" + (ev.files.length === 1 ? "" : "s");
      }
    }
    buildIssues = ev.issues || [];
    if (ev.renamed) {
      document.querySelectorAll("[data-project-name]").forEach((el) => {
        el.textContent = ev.renamed;
      });
      const label = document.querySelector("[data-label-field]");
      if (label) label.value = ev.renamed;
      document.querySelectorAll("[data-rename-open]").forEach((btn) => {
        btn.dataset.renameName = ev.renamed;
      });
      document.title = ev.renamed + " · Studio";
      const note = document.createElement("p");
      note.className = "msg-note";
      note.textContent = "✨ Named your project “" + ev.renamed + "”. Tap the pencil next to the name to change it.";
      bubble.el.appendChild(note);
    }
    if (ev.applied && ev.applied.length) {
      markBuilt();
      buddy.say("Ta-da! Here it comes… 🎉");
      autoFixArmed = !bubble.isAutoFix;
      armedAt = Date.now();
      focusPreviewOnLoad = true;
      reloadPreview();
    } else {
      afterFailedBuild();
      renderIssues();
    }
  }

  // One automatic repair per build: if the new preview throws within a few seconds of loading,
  // send the errors back as a fix build. Never chains (a failed auto-fix leaves the button).
  let autoFixArmed = false;
  let armedAt = 0;
  let autoFixTimer = null;
  onRuntimeError = () => {
    if (!autoFixArmed || busy || Date.now() - armedAt > 20000) return;
    clearTimeout(autoFixTimer);
    autoFixTimer = setTimeout(() => {
      if (!autoFixArmed || busy || !runtimeErrors.length) return;
      autoFixArmed = false;
    if (intent === "clarify") {
      pendingChanges += 1;
      if (form.dataset.hasBuilt) setStep("build", "is-current");
    }
      send(
        "build",
        "The preview crashed straight after that build. Fix these errors (keep everything else the same):\n" +
          runtimeErrors.map((m) => "- " + m).join("\n"),
        { auto: true }
      );
    }, 1200); // let related errors arrive together
  };

  function afterFailedBuild() {
    if (form.dataset.hasBuilt) buddy.hide();
    else {
      buddy.show("idle");
      buddy.say(BUDDY_LINES.oops);
    }
  }

  function send(intent, text, opts) {
    const auto = Boolean(opts && opts.auto);
    const fd = new FormData(form);
    fd.set("intent", intent);
    fd.set("message", text);
    if (intentField) intentField.value = intent;
    thread?.querySelector("[data-suggestions]")?.remove();
    const userBubble = addMsg(auto ? "assistant" : "user", '<div class="msg-body is-md"></div>', auto ? "msg-auto" : "");
    setMd(
      userBubble.querySelector(".msg-body"),
      auto ? "🔧 **Oops, I spotted a bug in the preview.** Fixing it now, no need to do anything!" : text
    );
    autoFixArmed = false;
    if (intent === "clarify") {
      pendingChanges += 1;
      if (form.dataset.hasBuilt) setStep("build", "is-current");
    }
    if (chatInput) {
      chatInput.value = "";
      autoGrow();
    }
    markDescribed();
    setBusy(true, intent);
    const bubble = pendingBubble(intent);
    bubble.isAutoFix = auto;
    if (auto) buddy.say("Oops, I spotted a bug. Fixing it! 🔧");
    controller = new AbortController();
    let finished = false;

    const handle = (ev) => {
      if (ev.type === "status") {
        if (ev.stage === "thinking") bubble.status(intent === "build" ? "Planning the build…" : "Thinking…");
        if (ev.stage === "checking") bubble.status("Checking the files…", true);
      } else if (ev.type === "prose") {
        setMd(bubble.body, ev.text);
        scrollThread();
      } else if (ev.type === "files") {
        renderFiles(bubble, ev);
      } else if (ev.type === "done") {
        finished = true;
        bubble.finish();
        setMd(bubble.body, ev.message || "Done.");
        if (intent === "build") finishBuild(bubble, ev);
      } else if (ev.type === "error") {
        finished = true;
        bubble.finish();
        bubble.el.classList.add("msg-error");
        if (intent === "build") afterFailedBuild();
        bubble.files?.remove();
        setMd(bubble.body, "Something went wrong talking to the model: " + ev.message);
      }
    };

    fetch(chatUrl, {
      method: "POST",
      body: fd,
      headers: { Accept: "application/x-ndjson", "X-Requested-With": "fetch" },
      signal: controller.signal,
    })
      .then(async (res) => {
        const type = res.headers.get("content-type") || "";
        if (!res.ok || type.includes("application/json")) {
          const body = await res.json().catch(() => ({}));
          handle({ type: "error", message: body.message || "Request failed (" + res.status + ")." });
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let nl;
          while ((nl = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, nl).trim();
            buffer = buffer.slice(nl + 1);
            if (!line) continue;
            try {
              handle(JSON.parse(line));
            } catch (_err) {
              /* partial or junk line */
            }
          }
        }
        if (!finished) handle({ type: "error", message: "The connection closed before the reply finished." });
      })
      .catch((err) => {
        if (finished) return;
        if (err && err.name === "AbortError") {
          finished = true;
          bubble.finish();
          bubble.files?.remove();
          setMd(bubble.body, "_Stopped._ Nothing was changed.");
          if (intent === "build") afterFailedBuild();
        } else {
          handle({ type: "error", message: "Network error — is Studio still running?" });
        }
      })
      .finally(() => {
        controller = null;
        setBusy(false, intent);
        if (form.dataset.hasBuilt && !(buildIssues.length + runtimeErrors.length)) {
          setStatus("live", "Live from your last build");
        }
        if (!focusPreviewOnLoad) chatInput?.focus();
      });
  }

  stopBtn?.addEventListener("click", () => controller?.abort());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && busy) controller?.abort();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy) return;
    const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
    const intent = (submitter && submitter.getAttribute("data-intent")) || "clarify";
    let text = (chatInput?.value || "").trim();
    if (!text && intent === "build") {
      if (!form.dataset.hasDescribed) {
        chatInput?.focus();
        chatInput?.setAttribute("placeholder", "Describe your idea first, then press Build…");
        return;
      }
      text = form.dataset.hasBuilt
        ? "Rebuild with the changes we discussed."
        : "Build it from our conversation so far.";
    }
    if (!text) {
      chatInput?.focus();
      return;
    }
    send(intent, text);
  });

  fixBtn?.addEventListener("click", () => {
    if (busy) return;
    const all = buildIssues.concat(runtimeErrors);
    if (!all.length) return;
    send(
      "build",
      "The preview has these problems — please fix them:\n" + all.map((m) => "- " + m).join("\n")
    );
  });
})();
