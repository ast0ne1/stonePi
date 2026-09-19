(function () {
  "use strict";

  var STAR_OUTLINE = '<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';
  var STAR_FILLED = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor" stroke="currentColor"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';

  // --- Theme & Palette Management ---
  var THEME_KEY = "stonepi-theme";
  var PALETTE_KEY = "stonepi-palette";
  var LEGACY_THEME_KEYS = ["newscast-theme", "fileserve-theme", "eventtrakr-theme"];
  var LEGACY_PALETTE_KEYS = ["newscast-palette", "fileserve-palette", "eventtrakr-palette"];
  var PALETTES = ["default", "ocean", "forest", "slate"];
  var THEME_COLORS = {
    default: { light: "#f3eee4", dark: "#12100d" },
    ocean: { light: "#e7eef5", dark: "#0c141c" },
    forest: { light: "#eef1e6", dark: "#10140d" },
    slate: { light: "#ececee", dark: "#121314" },
  };

  function syncTopbarHeight() {
    var topbar = document.querySelector(".topbar");
    if (!topbar) return;
    var height = Math.ceil(topbar.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--topbar-height", height + "px");
  }

  function readShared(shared, legacy, fallback) {
    var value = localStorage.getItem(shared);
    if (value) return value;
    for (var i = 0; i < legacy.length; i++) {
      value = localStorage.getItem(legacy[i]);
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
    var value = readShared(PALETTE_KEY, LEGACY_PALETTE_KEYS, "default");
    return PALETTES.indexOf(value) >= 0 ? value : "default";
  }

  function applyTheme(pref, palette) {
    var theme = resolvedTheme(pref);
    var chosen = palette || savedPalette();
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.themePref = pref;
    document.documentElement.dataset.palette = chosen;
    document.documentElement.style.colorScheme = theme;
    localStorage.setItem(THEME_KEY, pref);
    localStorage.setItem(PALETTE_KEY, chosen);
    var meta = document.querySelector("[data-theme-color]");
    if (meta && THEME_COLORS[chosen]) meta.setAttribute("content", THEME_COLORS[chosen][theme]);
    document.querySelectorAll("[data-theme-set]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.themeSet === pref);
    });
    document.querySelectorAll("[data-palette-set]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.dataset.paletteSet === chosen);
    });
  }

  var savedTheme = readShared(THEME_KEY, LEGACY_THEME_KEYS, "system");
  applyTheme(savedTheme);
  syncTopbarHeight();
  window.addEventListener("resize", syncTopbarHeight);
  window.addEventListener("orientationchange", syncTopbarHeight);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", syncTopbarHeight);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(syncTopbarHeight);
  if (typeof ResizeObserver === "function") {
    var topbarEl = document.querySelector(".topbar");
    if (topbarEl) new ResizeObserver(syncTopbarHeight).observe(topbarEl);
  }

  document.querySelectorAll("[data-theme-set]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyTheme(btn.dataset.themeSet);
    });
  });
  document.querySelectorAll("[data-palette-set]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyTheme(localStorage.getItem(THEME_KEY) || "system", btn.dataset.paletteSet);
    });
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    if ((localStorage.getItem(THEME_KEY) || "system") === "system") applyTheme("system");
  });

  // Initialize remaining controls on DOM ready
  document.addEventListener("DOMContentLoaded", function () {
    syncTopbarHeight();

    // --- Dropdown Management ---
    document.addEventListener("click", function (e) {
      var trigger = e.target.closest(".dropdown-toggle");
      document.querySelectorAll(".dropdown-content.show").forEach(function (menu) {
        if (!trigger || menu !== trigger.nextElementSibling) {
          menu.classList.remove("show");
        }
      });
      if (trigger) {
        var menu = trigger.nextElementSibling;
        if (menu && menu.classList.contains("dropdown-content")) {
          menu.classList.toggle("show");
        }
      }
    });

    // --- Favourite / Star Toggle with Google Calendar Forwarding ---
    var favouritesView = document.querySelector("[data-favourites-view]");
    var isFavouritesView = !!favouritesView && favouritesView.dataset.favouritesView === "1";

    function removeEventCard(eventId) {
      var card = document.querySelector('[data-event-card="' + eventId + '"]');
      if (!card) return;
      var grid = card.parentElement;
      card.style.transition = "opacity 180ms ease";
      card.style.opacity = "0";
      setTimeout(function () {
        card.remove();
        if (grid && !grid.querySelector("[data-event-card]")) {
          var empty = document.createElement("div");
          empty.className = "card empty-state";
          empty.innerHTML =
            '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>' +
            "<h3>No favourites yet</h3>" +
            '<p style="margin-top: 6px;">Star an event to see it here.</p>';
          grid.replaceWith(empty);
        }
      }, 180);
    }

    document.querySelectorAll("[data-fav-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var eventId = btn.dataset.favToggle;
        btn.disabled = true;

        fetch("/api/events/" + eventId + "/favourite", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        })
          .then(function (res) {
            return res.json().then(function (data) {
              return { ok: res.ok, data: data };
            });
          })
          .then(function (result) {
            btn.disabled = false;
            var data = result.data || {};
            if (!result.ok || typeof data.favourited !== "boolean") {
              showToast(data.error || "Could not update favourite");
              return;
            }
            if (data.favourited) {
              btn.classList.add("starred");
              btn.innerHTML = STAR_FILLED;
              btn.title = "Favourited";
              if (data.calendar_synced) {
                showToast("⭐ Added to Favourites & Synced to Google Calendar!");
              } else {
                showToast("⭐ Added to Favourites");
              }
            } else {
              btn.classList.remove("starred");
              btn.innerHTML = STAR_OUTLINE;
              btn.title = "Add to Favourites";
              showToast("Removed from Favourites");
              if (isFavouritesView) {
                removeEventCard(eventId);
              }
            }
          })
          .catch(function (err) {
            btn.disabled = false;
            showToast("Could not update favourite");
            console.error("Error toggling favourite:", err);
          });
      });
    });

    // --- Global schedule editor (interval vs. specific days & times) ---
    var scheduleTypeSelect = document.querySelector("[data-schedule-type]");
    if (scheduleTypeSelect) {
      var scheduleForm = scheduleTypeSelect.closest("[data-schedule-global-form]");

      function syncSchedulePanels() {
        var mode = scheduleTypeSelect.value;
        scheduleForm.querySelectorAll("[data-schedule-panel]").forEach(function (panel) {
          panel.hidden = panel.dataset.schedulePanel !== mode;
        });
      }
      scheduleTypeSelect.addEventListener("change", syncSchedulePanels);

      var timeList = scheduleForm.querySelector("[data-time-list]");
      var addTimeBtn = scheduleForm.querySelector("[data-add-time]");

      function bindRemoveButton(row) {
        var btn = row.querySelector("[data-remove-time]");
        btn.addEventListener("click", function () {
          if (timeList.querySelectorAll("[data-time-row]").length > 1) {
            row.remove();
          }
        });
      }

      timeList.querySelectorAll("[data-time-row]").forEach(bindRemoveButton);

      if (addTimeBtn) {
        addTimeBtn.addEventListener("click", function () {
          var row = timeList.querySelector("[data-time-row]").cloneNode(true);
          row.querySelector('input[type="time"]').value = "09:00";
          timeList.appendChild(row);
          bindRemoveButton(row);
        });
      }
    }

    // --- Per-source schedule editor (show/hide custom interval field) ---
    document.querySelectorAll("[data-schedule-mode]").forEach(function (select) {
      var form = select.closest("[data-schedule-form]");
      var intervalField = form && form.querySelector("[data-schedule-interval]");
      if (!intervalField) return;
      select.addEventListener("change", function () {
        intervalField.hidden = select.value !== "custom";
      });
    });

    // --- Sync All & Status Pill Polling ---
    var syncPill = document.querySelector("[data-sync-pill]");
    var syncBtn = document.querySelector("[data-sync-btn]");

    function updateSyncStatus() {
      fetch("/api/sync/status")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!syncPill) return;
          if (data.running) {
            syncPill.textContent = "Syncing";
            syncPill.classList.add("is-busy");
            if (syncBtn) {
              syncBtn.disabled = true;
              syncBtn.classList.add("is-busy");
            }
            setTimeout(updateSyncStatus, 2500);
          } else {
            syncPill.classList.remove("is-busy");
            syncPill.textContent = data.last_error ? "Error" : "Idle";
            if (syncBtn) {
              syncBtn.disabled = false;
              syncBtn.classList.remove("is-busy");
            }
          }
        })
        .catch(function () {});
    }

    if (syncBtn) {
      syncBtn.addEventListener("click", function () {
        syncBtn.disabled = true;
        syncBtn.classList.add("is-busy");
        if (syncPill) {
          syncPill.textContent = "Syncing";
          syncPill.classList.add("is-busy");
        }
        fetch("/api/sync/trigger", { method: "POST" })
          .then(function () {
            setTimeout(updateSyncStatus, 1500);
          })
          .catch(function () {
            if (syncBtn) {
              syncBtn.disabled = false;
              syncBtn.classList.remove("is-busy");
            }
          });
      });
    }

    // Initial check
    updateSyncStatus();

    // --- Per-source Sync Button (Sources page) ---
    var sourceSyncBtns = document.querySelectorAll("[data-sync-source-btn]");
    var sourceSyncStatus = document.querySelector("[data-source-sync-status]");
    var sourceSyncPolling = false;

    function setSourceButtonsBusy(busy) {
      sourceSyncBtns.forEach(function (btn) {
        btn.disabled = busy;
        btn.classList.toggle("is-busy", busy);
      });
    }

    function pollSourceSync() {
      fetch("/api/sync/status")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.running) {
            setSourceButtonsBusy(true);
            if (sourceSyncStatus) {
              sourceSyncStatus.hidden = false;
              sourceSyncStatus.textContent = (data.progress || "A sync is running") + " -- this can take a few minutes for some sources, please wait.";
            }
            setTimeout(pollSourceSync, 2000);
          } else {
            sourceSyncPolling = false;
            setSourceButtonsBusy(false);
            if (sourceSyncStatus && !sourceSyncStatus.hidden) {
              sourceSyncStatus.textContent = data.last_error ? "Sync failed: " + data.last_error : (data.progress || "Sync complete.");
              setTimeout(function () { window.location.reload(); }, 800);
            }
          }
        })
        .catch(function () {
          sourceSyncPolling = false;
          setSourceButtonsBusy(false);
        });
    }

    function startSourcePolling() {
      if (sourceSyncPolling) return;
      sourceSyncPolling = true;
      pollSourceSync();
    }

    sourceSyncBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        // Buttons are already disabled whenever a sync is in flight (both by
        // the initial-state check below and by this same handler), but a
        // click that sneaks in between polls before the disabled attribute
        // takes visible effect must still not fire a second request.
        if (btn.disabled) return;

        var sourceId = btn.getAttribute("data-sync-source-btn");
        var sourceName = btn.getAttribute("data-source-name") || "source";
        setSourceButtonsBusy(true);
        if (sourceSyncStatus) {
          sourceSyncStatus.hidden = false;
          sourceSyncStatus.textContent = "Syncing '" + sourceName + "'... this can take a few minutes for some sources, please wait.";
        }
        fetch("/api/sources/" + sourceId + "/sync", { method: "POST" })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            if (!data.started) {
              // Something else (another source, or the global "Sync All")
              // is already running -- keep the buttons disabled and reflect
              // its real progress instead of silently dropping the click.
              if (sourceSyncStatus) sourceSyncStatus.textContent = "Another sync is already running -- showing its progress instead.";
              startSourcePolling();
              return;
            }
            setTimeout(startSourcePolling, 1000);
          })
          .catch(function () {
            setSourceButtonsBusy(false);
          });
      });
    });

    // If a sync (from this source's button, the global "Sync All" button, or
    // another tab/device entirely) is already running when this page loads,
    // reflect that immediately instead of showing idle, clickable buttons
    // that would just queue up requests the backend silently rejects.
    if (sourceSyncBtns.length) {
      fetch("/api/sync/status")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.running) startSourcePolling();
        })
        .catch(function () {});
    }

    // --- Add Event: Fetch from Facebook ---
    var fbFetchBtn = document.getElementById("fb_fetch_btn");
    if (fbFetchBtn) {
      var fbUrlInput = document.getElementById("fb_event_url");
      var fbStatus = document.getElementById("fb_fetch_status");

      fbFetchBtn.addEventListener("click", function () {
        var url = (fbUrlInput.value || "").trim();
        if (!url) {
          fbStatus.hidden = false;
          fbStatus.textContent = "Paste a Facebook event URL first.";
          return;
        }
        fbFetchBtn.disabled = true;
        fbStatus.hidden = false;
        fbStatus.textContent = "Fetching event details... this can take up to a couple of minutes.";

        fetch("/add-event/fetch-facebook", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url }),
        })
          .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
          .then(function (result) {
            fbFetchBtn.disabled = false;
            if (!result.ok) {
              fbStatus.textContent = result.data.error || "Could not fetch that event.";
              return;
            }
            var data = result.data;
            fbStatus.textContent = "Fetched -- review the details below before saving.";
            document.getElementById("title").value = data.title || "";
            document.getElementById("description").value = data.description || "";
            document.getElementById("location").value = data.location || "";
            document.getElementById("cost").value = data.cost || "";
            document.getElementById("url").value = data.url || "";
            document.getElementById("image_url").value = data.image_url || "";
            if (data.start_time) {
              var startParts = data.start_time.split("T");
              document.getElementById("date").value = startParts[0] || "";
              document.getElementById("time").value = startParts[1] || "";
            }
            if (data.end_time) {
              var endParts = data.end_time.split("T");
              document.getElementById("end_date").value = endParts[0] || "";
              document.getElementById("end_time").value = endParts[1] || "";
            }
          })
          .catch(function () {
            fbFetchBtn.disabled = false;
            fbStatus.textContent = "Something went wrong fetching that event.";
          });
      });
    }
  });

  function showToast(msg) {
    var existing = document.querySelector(".toast");
    if (existing) existing.remove();

    var toast = document.createElement("div");
    toast.className = "toast is-on is-ok";
    toast.textContent = msg;
    document.body.appendChild(toast);

    setTimeout(function () {
      toast.classList.remove("is-on");
      setTimeout(function () { toast.remove(); }, 200);
    }, 3200);
  }

  function askConfirm(opts) {
    var sheet = document.querySelector("[data-confirm-sheet]");
    if (!sheet) return Promise.resolve(window.confirm(opts.title));
    return new Promise(function (resolve) {
      var titleEl = sheet.querySelector("[data-confirm-title]");
      var bodyEl = sheet.querySelector("[data-confirm-body]");
      var okBtn = sheet.querySelector("[data-confirm-ok]");
      var cancelBtn = sheet.querySelector("[data-confirm-cancel]");
      if (titleEl) titleEl.textContent = opts.title;
      if (bodyEl) {
        bodyEl.hidden = !opts.body;
        bodyEl.textContent = opts.body || "";
      }
      if (okBtn) okBtn.textContent = opts.okLabel || "Remove";
      function finish(value) {
        sheet.hidden = true;
        sheet.classList.remove("is-open");
        sheet.removeEventListener("click", onBackdrop);
        if (okBtn) okBtn.removeEventListener("click", onOk);
        if (cancelBtn) cancelBtn.removeEventListener("click", onCancel);
        document.removeEventListener("keydown", onKey);
        resolve(value);
      }
      function onOk() { finish(true); }
      function onCancel() { finish(false); }
      function onBackdrop(event) {
        if (event.target === sheet) finish(false);
      }
      function onKey(event) {
        if (event.key === "Escape") finish(false);
      }
      sheet.addEventListener("click", onBackdrop);
      if (okBtn) okBtn.addEventListener("click", onOk);
      if (cancelBtn) cancelBtn.addEventListener("click", onCancel);
      document.addEventListener("keydown", onKey);
      sheet.hidden = false;
      sheet.classList.add("is-open");
      if (okBtn) okBtn.focus();
    });
  }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmPassed === "1") {
        delete form.dataset.confirmPassed;
        return;
      }
      event.preventDefault();
      askConfirm({
        title: form.dataset.confirm,
        body: form.dataset.confirmDetail || "",
        okLabel: form.dataset.confirmOk || "Continue",
      }).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmPassed = "1";
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.submit();
      });
    });
  });

  var settingsRoot = document.querySelector("[data-settings-tabs]");
  if (settingsRoot) {
    var settingsChips = settingsRoot.querySelectorAll("[data-settings-tab]");
    var settingsLede = document.querySelector("[data-settings-lede]");

    function showSettingsTab(tab) {
      var next = Array.prototype.some.call(settingsChips, function (chip) {
        return chip.dataset.settingsTab === tab;
      })
        ? tab
        : "general";
      settingsChips.forEach(function (chip) {
        var on = chip.dataset.settingsTab === next;
        chip.classList.toggle("is-active", on);
        chip.setAttribute("aria-selected", on ? "true" : "false");
      });
      settingsRoot.querySelectorAll("[data-settings-panel]").forEach(function (panel) {
        panel.hidden = panel.dataset.settingsPanel !== next;
      });
      var activeChip = Array.prototype.find.call(settingsChips, function (chip) {
        return chip.dataset.settingsTab === next;
      });
      if (settingsLede && activeChip && activeChip.dataset.settingsLede) {
        settingsLede.textContent = activeChip.dataset.settingsLede;
      }
      var url = new URL(window.location.href);
      url.searchParams.set("tab", next);
      window.history.replaceState(null, "", url);
    }

    settingsChips.forEach(function (chip) {
      chip.addEventListener("click", function (event) {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        showSettingsTab(chip.dataset.settingsTab);
      });
    });
  }
})();
