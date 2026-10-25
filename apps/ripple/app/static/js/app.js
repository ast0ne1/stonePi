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

// Post form: photo previews with captions, quick checks before sending, and an upload with
// progress. The server answers JSON (X-Ripple-Ajax), so an error leaves the form as it is.
// Without this script the form still posts normally (photos without captions).
function setupPostForm(form) {
  const input = form.querySelector("[data-photo-input]");
  const list = form.querySelector("[data-new-photos]");
  const template = document.querySelector("[data-photo-template]");
  const errorEl = form.querySelector("[data-form-error]");
  const progress = form.querySelector("[data-upload-progress]");
  const bar = progress ? progress.querySelector("progress") : null;
  const label = form.querySelector("[data-upload-label]");
  const submit = form.querySelector("[data-submit]");
  const maxPhotos = Number(form.dataset.maxPhotos || 4);
  const maxBytes = Number(form.dataset.maxBytes || 0);
  const types = ["image/jpeg", "image/png", "image/webp"];
  const picked = [];

  const showError = (message) => {
    errorEl.textContent = message || "";
    errorEl.hidden = !message;
    if (message) errorEl.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const kept = () =>
    form.querySelectorAll("[data-existing-photo]").length -
    form.querySelectorAll("[data-remove-photo]:checked").length;

  input.addEventListener("change", () => {
    showError("");
    for (const file of Array.from(input.files || [])) {
      if (kept() + picked.length >= maxPhotos) {
        showError(`A post can have up to ${maxPhotos} photo${maxPhotos === 1 ? "" : "s"}.`);
        break;
      }
      if (file.type && !types.includes(file.type)) {
        showError(`${file.name} isn't a JPEG, PNG or WebP photo.`);
        continue;
      }
      if (maxBytes && file.size > maxBytes) {
        showError(`${file.name} is over ${Math.round(maxBytes / 1048576)} MB.`);
        continue;
      }
      const row = template.content.firstElementChild.cloneNode(true);
      const url = URL.createObjectURL(file);
      row.querySelector("img").src = url;
      const entry = { file, row, url };
      row.querySelector(".photo-drop").addEventListener("click", () => {
        URL.revokeObjectURL(url);
        row.remove();
        picked.splice(picked.indexOf(entry), 1);
      });
      list.appendChild(row);
      picked.push(entry);
    }
    input.value = "";
  });

  form.addEventListener("submit", (event) => {
    if (!window.FormData || !window.XMLHttpRequest) return;
    event.preventDefault();
    showError("");
    const data = new FormData(form);
    data.delete("photos");
    data.delete("captions");
    for (const entry of picked) {
      data.append("photos", entry.file, entry.file.name);
      data.append("captions", entry.row.querySelector('[name="captions"]').value);
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-Ripple-Ajax", "1");
    submit.disabled = true;
    if (picked.length && progress) {
      progress.hidden = false;
      bar.value = 0;
      label.textContent = "Uploading…";
    }
    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable || !bar) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.value = pct;
      label.textContent = pct < 100 ? `Uploading… ${pct}%` : "Saving photos…";
    });
    const finish = (message) => {
      submit.disabled = false;
      if (progress) progress.hidden = true;
      showError(message);
    };
    xhr.addEventListener("load", () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch (_err) {
        // Not JSON: signed out or no access. Show whatever page StonePi sent us to.
        window.location.href = xhr.responseURL || form.action;
        return;
      }
      if (body.ok) {
        window.location.href = body.redirect;
        return;
      }
      finish(body.error || "Couldn't save. Try again.");
    });
    xhr.addEventListener("error", () => finish("Couldn't reach StonePi. Check your connection and try again."));
    xhr.send(data);
  });
}

const postForm = document.querySelector("[data-post-form]");
if (postForm) setupPostForm(postForm);

// Settings → Family display: city pickers for the "here" strip (Open-Meteo place search).
document.querySelectorAll("[data-place-picker]").forEach((picker) => {
  const value = picker.querySelector("[data-place-value]");
  const label = picker.querySelector("[data-place-label]");
  const query = picker.querySelector("[data-place-query]");
  const results = picker.querySelector("[data-place-results]");
  const error = picker.querySelector("[data-place-error]");
  const showError = (message) => {
    error.textContent = message || "";
    error.hidden = !message;
  };
  const find = async () => {
    const q = query.value.trim();
    results.replaceChildren();
    showError("");
    if (q.length < 2) return;
    try {
      const response = await fetch(`${picker.dataset.href}?q=${encodeURIComponent(q)}`, {
        headers: { Accept: "application/json" },
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.message);
      if (!body.places.length) {
        showError("No places found. Try another spelling.");
        return;
      }
      for (const place of body.places) {
        const item = document.createElement("li");
        const choose = document.createElement("button");
        choose.type = "button";
        choose.className = "btn btn-ghost place-choice";
        choose.textContent = place.label;
        choose.addEventListener("click", () => {
          value.value = JSON.stringify(place);
          label.textContent = place.label;
          results.replaceChildren();
          query.value = "";
        });
        item.appendChild(choose);
        results.appendChild(item);
      }
    } catch (err) {
      showError((err && err.message) || "The place search isn't reachable right now.");
    }
  };
  picker.querySelector("[data-place-find]").addEventListener("click", find);
  query.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault(); // search, don't submit the settings form
      find();
    }
  });
  picker.querySelector("[data-place-clear]")?.addEventListener("click", () => {
    value.value = "";
    label.textContent = "Not chosen";
  });
});
