(function () {
  const THEME_KEY = "stonepi-theme";
  const LEGACY_THEME = ["newscast-theme", "fileserve-theme", "eventtrakr-theme"];

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

  applyTheme(readShared(THEME_KEY, LEGACY_THEME, "system"));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (readShared(THEME_KEY, LEGACY_THEME, "system") === "system") applyTheme("system");
  });

  // Done messages arrive as ?msg=… after a form post: show them as a toast that fades, and
  // drop msg/error from the address so a reload or Back doesn't bring them back.
  const toastEl = document.querySelector("[data-toast]");
  function toast(message, kind) {
    if (!toastEl || !message) return;
    toastEl.textContent = message;
    toastEl.classList.toggle("is-error", kind === "error");
    toastEl.hidden = false;
    window.requestAnimationFrame(() => toastEl.classList.add("is-on"));
    window.clearTimeout(toastEl._timer);
    toastEl._timer = window.setTimeout(() => {
      toastEl.classList.remove("is-on");
      toastEl.hidden = true;
    }, 4500);
  }
  window.radioToast = toast;
  if (toastEl && toastEl.dataset.toastMessage) toast(toastEl.dataset.toastMessage);

  function cleanAddress(href) {
    try {
      const url = new URL(href, window.location.href);
      url.searchParams.delete("msg");
      url.searchParams.delete("error");
      return url.pathname + (url.search || "") + url.hash;
    } catch {
      return href; // an odd address: leave it
    }
  }
  window.history.replaceState({ radio: 1 }, "", cleanAddress(window.location.href));

  // Page parts are wired up once each: after Radio swaps in a new page (navigate, below) the
  // same set-up runs again and only picks up the elements it hasn't seen.
  function each(selector, key, fn) {
    document.querySelectorAll(selector).forEach((el) => {
      const flag = "radio" + key;
      if (el.dataset[flag]) return;
      el.dataset[flag] = "1";
      fn(el);
    });
  }

  // A station logo that can't be had (404 from /art, broken image) is removed, leaving the
  // station's letter underneath. Capture phase: error events don't bubble.
  document.addEventListener(
    "error",
    (event) => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement)) return;
      if (img.hasAttribute("data-station-logo")) img.remove();
      // Now Playing's and the player bar's artwork: hide it (no broken-image "?") and show
      // the icon beside it instead.
      if (img.hasAttribute("data-player-art") || img.hasAttribute("data-art-fallback")) {
        img.dataset.failed = img.getAttribute("src") || "";
        img.hidden = true;
        img.removeAttribute("src");
        img.parentElement?.querySelector("[data-player-art-fallback], [data-art-icon]")?.removeAttribute("hidden");
      }
    },
    true,
  );

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

  function findSheet(name) {
    if (name) return document.querySelector(`[data-sheet="${name}"]`);
    return document.querySelector("[data-sheet]");
  }

  // Sheets live on <body> (fixed, over everything). A page's own sheets (inside <main>) are
  // marked so they go when Radio swaps in another page; Play on stays.
  function mountSheetsToBody() {
    document.querySelectorAll(".sheet").forEach((sheet) => {
      if (sheet.closest("main")) sheet.dataset.pageSheet = "1";
      if (sheet.parentElement !== document.body) document.body.appendChild(sheet);
    });
  }

  function openSheetEl(sheet) {
    if (!sheet) return;
    if (sheet.parentElement !== document.body) document.body.appendChild(sheet);
    sheet.hidden = false;
    sheet.classList.add("is-open");
    document.documentElement.classList.add("sheet-open");
    document.body.classList.add("sheet-open");
    window.setTimeout(() => sheet.querySelector("[data-autofocus]")?.focus(), 60);
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

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".sheet.is-open[data-sheet]").forEach((sheet) => closeSheetEl(sheet));
  });

  // ── Where Radio plays (radio-outputs.md) ────────────────────────────────────────────────
  // This device: an <audio> in the player bar. Radio changes page without reloading
  // (navigate, below), so the stream carries on; the station is also kept in sessionStorage
  // for a real reload (a browser may want a tap to restart it then). Speakers and StonePi:
  // the server plays, the page just asks. Background posts use the standard reply:
  // X-Requested-With: fetch → {ok, message, …}.
  const body = document.body;
  const PREFIX = (body.dataset.prefix || "").replace(/\/$/, "");
  const KEY = "radio-player";
  const audio = document.querySelector("[data-player-audio]");
  const bar = document.querySelector("[data-player-bar]");
  // Settings → Player bar: "device" (while This device plays), "always" (a speaker or
  // StonePi too), "off".
  let barMode = body.dataset.playerBarMode || "device";
  let server = null; // what /api/now said about your speaker or StonePi
  let hlsLib = null;
  let hls = null;

  function api(path, data) {
    const form = new URLSearchParams({ csrf_token: body.dataset.csrf || "", ...data });
    return fetch(PREFIX + path, {
      method: "POST",
      body: form,
      headers: { "X-Requested-With": "fetch", Accept: "application/json" },
    }).then((r) => r.json().catch(() => ({ ok: false, message: "Radio didn't answer properly." })));
  }

  function load() {
    try {
      return JSON.parse(sessionStorage.getItem(KEY) || "null");
    } catch {
      return null;
    }
  }
  function save(state) {
    try {
      if (state) sessionStorage.setItem(KEY, JSON.stringify(state));
      else sessionStorage.removeItem(KEY);
    } catch {
      /* private mode: the station just won't carry across pages */
    }
  }

  function loadHls() {
    if (hlsLib) return Promise.resolve(hlsLib);
    return new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = body.dataset.hlsSrc;
      tag.onload = () => resolve((hlsLib = window.Hls));
      tag.onerror = () => reject(new Error("Couldn't load the HLS player."));
      document.head.appendChild(tag);
    });
  }

  // Logo links in JSON replies are Radio paths (/art/…); pages get the /radio prefix added
  // on the way out, JSON doesn't, so add it here (once).
  function withPrefix(path) {
    if (!path || !path.startsWith("/") || !PREFIX || path.startsWith(PREFIX + "/")) return path || "";
    return PREFIX + path;
  }

  function showServer() {
    return barMode === "always" && !!server && server.output !== "browser" && !!(server.title || server.playing || server.paused);
  }

  function render() {
    const st = load();
    const playing = !!st && !!audio && !audio.paused;
    document.querySelectorAll("[data-player-title]").forEach((el) => {
      el.textContent = st ? st.name : "Pick a station";
    });
    document.querySelectorAll("[data-player-status]").forEach((el) => {
      if (!st) el.textContent = "Nothing playing";
      else if (st.needsTap && !playing) el.textContent = "Tap play to carry on";
      else if (playing && song && el.closest("[data-player-bar]")) el.textContent = "♪ " + song;
      else el.textContent = (playing ? "Playing" : "Paused") + " on This device";
    });
    document.querySelectorAll("[data-song]").forEach((el) => {
      el.hidden = !song;
      el.textContent = song;
    });
    document.querySelectorAll("[data-player-toggle]").forEach((btn) => {
      btn.disabled = !st;
      btn.querySelector("[data-icon-play]")?.toggleAttribute("hidden", playing);
      btn.querySelector("[data-icon-pause]")?.toggleAttribute("hidden", !playing);
      btn.setAttribute("aria-label", playing ? "Pause" : "Play");
    });
    document.querySelectorAll("[data-player-stop]").forEach((btn) => {
      btn.disabled = !st;
    });
    // An address that failed once (see the error handler) isn't tried again on every update.
    const playerArt = st && st.art && !document.querySelector(`[data-player-art][data-failed="${CSS.escape(st.art)}"]`) ? st.art : "";
    document.querySelectorAll("[data-player-art]").forEach((img) => {
      img.hidden = !playerArt;
      if (playerArt && img.getAttribute("src") !== playerArt) img.src = playerArt;
    });
    document.querySelectorAll("[data-player-art-fallback]").forEach((el) => {
      el.hidden = !!playerArt;
    });
    // Now's presets on This device: mark the one on air (the server marks a speaker's).
    if (document.querySelector("[data-now-browser]")) {
      document.querySelectorAll("[data-preset-url]").forEach((tile) => {
        tile.classList.toggle("is-on", !!st && tile.dataset.presetUrl === st.station_url);
      });
    }
    const vol = st && typeof st.volume === "number" ? st.volume : 0.8;
    document.querySelectorAll("[data-player-volume]").forEach((el) => {
      if (document.activeElement !== el) el.value = String(vol);
    });
    document.querySelectorAll("[data-player-volume-value]").forEach((el) => {
      el.textContent = Math.round(vol * 100) + "%";
    });
    if (bar) {
      // Now Playing has its own controls, and Settings is no place for it: no bar on either.
      const onNow = !!document.querySelector("[data-now-browser], [data-now-server], .settings-hub, .settings-panel");
      const fromServer = !st && showServer();
      bar.hidden = onNow || barMode === "off" || !(st || fromServer);
      if (fromServer) {
        // A speaker or StonePi (Wherever I play): what's on there, and its play/pause and stop.
        const on = !!server.playing;
        bar.querySelector("[data-player-title]").textContent = server.title || "Radio";
        bar.querySelector("[data-player-status]").textContent =
          on && song
            ? "♪ " + song + " · " + (server.output_name || "your speaker")
            : (on ? "Playing" : server.paused ? "Paused" : "Stopped") + " on " + (server.output_name || "your speaker");
        const toggle = bar.querySelector("[data-player-toggle]");
        toggle.disabled = false;
        toggle.querySelector("[data-icon-play]")?.toggleAttribute("hidden", on);
        toggle.querySelector("[data-icon-pause]")?.toggleAttribute("hidden", !on);
        toggle.setAttribute("aria-label", on ? "Pause" : "Play");
        bar.querySelector("[data-player-stop]").disabled = false;
        const art = bar.querySelector("[data-player-art]");
        const logo = withPrefix(server.art);
        if (art) {
          const usable = logo && art.dataset.failed !== logo;
          art.hidden = !usable;
          if (usable && art.getAttribute("src") !== logo) art.src = logo;
        }
      }
    }
    if (st && "mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: st.name,
        artist: "Radio",
        artwork: st.art ? [{ src: st.art, sizes: "160x160" }] : [],
      });
      navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    }
  }

  // Wherever I play: ask what's on your speaker or StonePi now and then (only while the page
  // is in view, and never while This device is playing).
  function pollServer() {
    if (barMode !== "always" || document.hidden || load()) return Promise.resolve();
    return fetch(PREFIX + "/api/now", { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        server = data && data.ok ? data : null;
        render();
      })
      .catch(() => {});
  }
  window.setInterval(pollServer, 10000);

  // The song on air, when the station sends it (ICY "Artist - Title"): This device asks about
  // its own station; a speaker or StonePi is looked up by the server. Every 20 s while playing.
  let song = "";
  function pollSong() {
    const st = load();
    const hereOn = !!st && !!audio && !audio.paused;
    const serverOn = !!document.querySelector("[data-now-playing]") || (showServer() && server.playing);
    if (document.hidden || !(hereOn || serverOn)) {
      if (song) {
        song = "";
        render();
      }
      return Promise.resolve();
    }
    const query = hereOn ? "?url=" + encodeURIComponent(st.station_url || "") : "";
    return fetch(PREFIX + "/api/song" + query, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        const next = (data && data.ok && data.song) || "";
        if (next !== song) {
          song = next;
          render();
        }
      })
      .catch(() => {});
  }
  window.setInterval(pollSong, 20000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollServer().then(pollSong);
  });

  function startAudio(st, opts) {
    const fromTap = !opts || opts.fromTap !== false;
    if (!audio) return Promise.resolve();
    if (hls) {
      hls.destroy();
      hls = null;
    }
    audio.volume = typeof st.volume === "number" ? st.volume : 0.8;
    const nativeHls = audio.canPlayType("application/vnd.apple.mpegurl");
    const begin = () =>
      audio.play().then(
        () => {
          st.needsTap = false;
          save(st);
          render();
        },
        (err) => {
          // A stream the browser won't load directly: once, through Radio's relay (same origin).
          if (err && err.name === "NotSupportedError" && !st.relayed && !st.hls) {
            st.relayed = true;
            return api("/api/relay", { url: st.src }).then((res) => {
              if (!res.ok) throw err;
              st.src = res.src;
              save(st);
              return startAudio(st, opts);
            });
          }
          // Autoplay rules: a restart after a page load may need a tap.
          st.needsTap = true;
          save(st);
          render();
          if (fromTap) toast(err && err.name === "NotAllowedError" ? "Tap play to start." : "This station won't play here.", "error");
        },
      );
    if (st.hls && !nativeHls) {
      return loadHls()
        .then((Hls) => {
          if (!Hls || !Hls.isSupported()) throw new Error("This browser can't play this station.");
          hls = new Hls();
          hls.loadSource(st.src);
          hls.attachMedia(audio);
          return begin();
        })
        .catch((err) => toast(err.message, "error"));
    }
    audio.src = st.src;
    return begin();
  }

  function playHere(source, art) {
    const prev = load();
    const st = {
      src: source.src,
      hls: !!source.hls,
      name: source.name,
      station_url: source.station_url,
      favicon: source.favicon || "",
      art: art || withPrefix(source.art),
      volume: prev && typeof prev.volume === "number" ? prev.volume : 0.8,
      playing: true,
    };
    server = null;
    save(st);
    render();
    return startAudio(st);
  }

  function stopHere() {
    if (!audio) return;
    if (hls) {
      hls.destroy();
      hls = null;
    }
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    save(null);
    render();
  }

  if (audio) {
    audio.addEventListener("play", () => {
      render();
      pollSong();
    });
    audio.addEventListener("pause", render);
    audio.addEventListener("error", () => {
      // A direct stream that won't load is retried through the relay (startAudio): only a
      // relayed or HLS stream failing is worth telling the person about.
      const st = load();
      if (st && audio.getAttribute("src") && (st.relayed || st.hls)) toast("The station stopped. Try again, or pick another.", "error");
      render();
    });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.setActionHandler("play", () => audio.play());
      navigator.mediaSession.setActionHandler("pause", () => audio.pause());
      navigator.mediaSession.setActionHandler("stop", stopHere);
    }
    // Carry on after a real page load.
    const st = load();
    if (st && st.playing) startAudio(st, { fromTap: false });
  }

  // A speaker or StonePi from the bar (Wherever I play).
  function serverTransport(action) {
    api("/transport", { action }).then((res) => {
      if (!res.ok) toast(res.message, "error");
      pollServer().then(pollSong);
    });
  }

  function setHereVolume(level) {
    const st = load();
    level = Math.max(0, Math.min(1, level));
    if (audio) audio.volume = level;
    if (st) {
      st.volume = level;
      save(st);
    }
    render();
  }

  // The output picker sheet.
  const outputSheet = document.querySelector('[data-sheet="outputs"]');
  const outputList = document.querySelector("[data-output-list]");
  function ago(seconds) {
    const mins = Math.round((Date.now() / 1000 - seconds) / 60);
    return mins < 60 ? "not seen for " + mins + " min" : "not seen for over an hour";
  }
  function showOutputs(refresh) {
    if (!outputList) return;
    if (refresh) outputList.innerHTML = '<p class="hint">Looking for speakers…</p>';
    fetch(PREFIX + "/api/outputs" + (refresh ? "?refresh=1" : ""), { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        outputList.innerHTML = "";
        (data.outputs || []).forEach((out) => {
          const row = document.createElement("button");
          row.type = "button";
          row.className = "output-row" + (out.id === data.current ? " is-current" : "");
          const icon = document.createElement("span");
          icon.className = "output-icon";
          const tpl = document.querySelector(`template[data-output-icon="${out.kind}"]`);
          if (tpl) icon.append(tpl.content.cloneNode(true));
          const copy = document.createElement("span");
          copy.className = "output-copy";
          const name = document.createElement("strong");
          name.textContent = out.name;
          const detail = document.createElement("span");
          detail.className = "hint";
          detail.textContent = out.stale ? out.detail + " · " + ago(out.last_seen) : out.detail;
          copy.append(name, detail);
          row.append(icon, copy);
          row.addEventListener("click", () => chooseOutput(out.id));
          outputList.appendChild(row);
        });
        if (!(data.outputs || []).some((o) => o.kind === "speaker")) {
          const none = document.createElement("p");
          none.className = "hint";
          none.textContent = "No speakers found yet. They need to be switched on and on the same Wi-Fi.";
          outputList.appendChild(none);
        }
      })
      .catch(() => {
        outputList.innerHTML = '<p class="hint">Couldn’t load the list. Try again.</p>';
      });
  }
  // The page again (its output chip, Now's card), without stopping This device.
  function refreshPage(message, kind) {
    return navigate(window.location.href, { push: false }).then(() => toast(message, kind));
  }
  function chooseOutput(id) {
    const st = load();
    const data = { output: id };
    if (st && audio && !audio.paused) Object.assign(data, { station_url: st.station_url, name: st.name, favicon: st.favicon || "" });
    api("/output", data).then((res) => {
      closeSheetEl(outputSheet);
      if (!res.ok) {
        // Not switched (a station the speaker can't reach): it keeps playing where it was.
        // Switched but couldn't start there (res.output): the page shows the new output.
        if (res.output) {
          stopHere();
          refreshPage(res.message, "error");
        } else {
          toast(res.message, "error");
        }
        return;
      }
      server = null;
      if (id === "browser") {
        if (res.browser) playHere(res.browser, st && st.art);
      } else {
        stopHere();
      }
      refreshPage(res.message).then(pollServer);
    });
  }

  // ── Everything a page brings (run on load and after each page swap) ─────────────────────
  function initPage() {
    // Now page volume: the slider applies as it moves (posted after a short pause, and on
    // release); − / + post in the background too and move the slider to the new level.
    each("form[data-volume]", "Volume", (form) => {
      const slider = form.querySelector("input[name=level]");
      const label = form.querySelector("[data-volume-value]");
      const token = form.querySelector("input[name=csrf_token]")?.value || "";
      let timer = 0;
      const show = (level) => {
        if (label) label.textContent = Math.round(level * 100) + "%";
      };
      const send = (field, value) => {
        const data = new URLSearchParams({ csrf_token: token, [field]: String(value) });
        return fetch(form.action, { method: "POST", body: data, headers: { "X-Requested-With": "fetch" } })
          .then((r) => r.json())
          .then((res) => {
            if (!res.ok) throw new Error(res.message || "Couldn't change the volume.");
            if (typeof res.volume === "number") {
              if (slider && document.activeElement !== slider) slider.value = String(res.volume);
              show(res.volume);
            }
          })
          .catch((err) => toast(err.message, "error"));
      };
      slider?.addEventListener("input", () => {
        show(Number(slider.value));
        window.clearTimeout(timer);
        timer = window.setTimeout(() => send("level", slider.value), 250);
      });
      slider?.addEventListener("change", () => {
        window.clearTimeout(timer);
        send("level", slider.value);
      });
      form.addEventListener("submit", (event) => {
        const button = event.submitter;
        if (!(button instanceof HTMLButtonElement) || button.name !== "delta") return;
        event.preventDefault();
        send("delta", button.value);
      });
    });

    // Settings → General: a slider's percentage follows it.
    each("input[data-range-value]", "Range", (el) =>
      el.addEventListener("input", () => {
        const out = el.parentElement.querySelector("output");
        if (out) out.textContent = Math.round(Number(el.value) * 100) + "%";
      }),
    );

    document.querySelectorAll("img[data-station-logo]").forEach((img) => {
      if (img.complete && img.naturalWidth === 0) img.remove();
    });

    each("form[data-confirm]", "Confirm", (form) => {
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

    each("button[type='submit'][data-confirm]", "Confirm", (button) => {
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

    mountSheetsToBody();
    each("[data-open-sheet]", "OpenSheet", (btn) => {
      btn.addEventListener("click", (event) => {
        const sheet = findSheet(btn.getAttribute("data-open-sheet") || "");
        if (!sheet) return; // the link's own page (e.g. /?add=1) opens it instead
        event.preventDefault();
        openSheetEl(sheet);
      });
    });
    each("[data-sheet]", "Sheet", (sheet) => {
      sheet.querySelectorAll("[data-close-sheet]").forEach((closeBtn) => {
        closeBtn.addEventListener("click", () => closeSheetEl(sheet));
      });
      sheet.addEventListener("click", (event) => {
        if (event.target === sheet) closeSheetEl(sheet);
      });
    });

    each("[data-player-toggle]", "Toggle", (btn) =>
      btn.addEventListener("click", () => {
        const st = load();
        if (!st && btn.closest("[data-player-bar]") && showServer()) {
          serverTransport("play_pause");
          return;
        }
        if (!st || !audio) return;
        if (audio.paused) {
          st.playing = true;
          save(st);
          if (!audio.getAttribute("src") && !hls) startAudio(st);
          else audio.play().catch(() => startAudio(st));
        } else {
          st.playing = false;
          save(st);
          audio.pause();
        }
      }),
    );
    each("[data-player-stop]", "Stop", (btn) =>
      btn.addEventListener("click", () => {
        if (!load() && btn.closest("[data-player-bar]") && showServer()) serverTransport("stop");
        else stopHere();
      }),
    );
    each("[data-player-volume]", "HereVolume", (el) => el.addEventListener("input", () => setHereVolume(Number(el.value))));
    each("[data-player-step]", "Step", (btn) =>
      btn.addEventListener("click", () => {
        const st = load();
        setHereVolume((st && typeof st.volume === "number" ? st.volume : 0.8) + Number(btn.dataset.playerStep));
      }),
    );

    // Play buttons (station cards, Now's presets): played on your output in the background.
    each("form.station-play, form.station-play-form", "Play", (form) =>
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const data = Object.fromEntries(new FormData(form).entries());
        delete data.csrf_token;
        const card = form.closest(".station-card");
        const logo = card && card.querySelector("img[data-station-logo]");
        api("/play", data).then((res) => {
          if (!res.ok) {
            toast(res.message, "error");
            return;
          }
          if (res.browser) {
            playHere(res.browser, logo ? logo.getAttribute("src") : "");
            return;
          }
          stopHere();
          if (document.querySelector("[data-now-server]")) refreshPage(res.message);
          else {
            toast(res.message);
            pollServer().then(pollSong);
          }
        });
      }),
    );

    each("[data-open-outputs]", "Outputs", (btn) =>
      btn.addEventListener("click", () => {
        openSheetEl(outputSheet);
        showOutputs(false);
      }),
    );
    each("[data-outputs-refresh]", "Refresh", (btn) => btn.addEventListener("click", () => showOutputs(true)));

    render();
  }

  // ── Changing page without stopping the stream ───────────────────────────────────────────
  // Links, searches, filters and forms inside Radio fetch the next page and swap in its
  // <main>, bottom nav and + button; the header, the player bar (with its <audio>) and Play on
  // stay put, so This device keeps playing. Anything else (other apps, Log out, downloads)
  // loads as usual, and so does any answer that isn't a Radio page.
  function inApp(url) {
    if (url.origin !== window.location.origin) return false;
    let path = url.pathname;
    if (PREFIX) {
      if (path !== PREFIX && !path.startsWith(PREFIX + "/")) return false;
      path = path.slice(PREFIX.length) || "/";
    }
    return !/^\/(logout|static|art|relay|api|healthz)(\/|$)/.test(path);
  }

  let navSeq = 0;
  function navigate(href, opts) {
    opts = opts || {};
    const seq = ++navSeq;
    const init = { method: opts.method || "GET", headers: { Accept: "text/html" }, credentials: "same-origin" };
    if (opts.body) init.body = opts.body;
    const fallback = (url) => {
      // A GET can simply load; a post that didn't come back as a Radio page is shown as it came.
      window.location.href = url;
    };
    return fetch(href, init)
      .then((res) => {
        if (seq !== navSeq) return null;
        const finalUrl = new URL(res.url || href, window.location.href);
        const type = res.headers.get("content-type") || "";
        if (!type.includes("text/html") || !inApp(finalUrl)) {
          fallback(finalUrl.href);
          return null;
        }
        return res.text().then((html) => ({ html, finalUrl }));
      })
      .then((got) => {
        if (!got || seq !== navSeq) return;
        const doc = new DOMParser().parseFromString(got.html, "text/html");
        const nextMain = doc.querySelector("main.main");
        const main = document.querySelector("main.main");
        if (!nextMain || !main) {
          fallback(got.finalUrl.href);
          return;
        }
        document.querySelectorAll("body > .sheet[data-page-sheet]").forEach((sheet) => sheet.remove());
        // The header stays, so close what a full load would have: search, bell, app switcher.
        document.querySelectorAll("[data-sp-drop]").forEach((drop) => {
          drop.hidden = true;
        });
        document.querySelectorAll("[data-sp-trigger]").forEach((btn) => btn.setAttribute("aria-expanded", "false"));
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
        document.documentElement.classList.remove("sheet-open");
        body.classList.remove("sheet-open");
        main.replaceWith(nextMain);
        for (const selector of ["nav.nav", "[data-page-fab]"]) {
          const now = document.querySelector(selector);
          const next = doc.querySelector(selector);
          if (now && next) now.replaceWith(next);
        }
        document.title = doc.title;
        if (doc.body.dataset.csrf) body.dataset.csrf = doc.body.dataset.csrf;
        if (doc.body.dataset.playerBarMode) barMode = body.dataset.playerBarMode = doc.body.dataset.playerBarMode;
        if (doc.body.dataset.playerPos) body.dataset.playerPos = doc.body.dataset.playerPos;
        const address = cleanAddress(got.finalUrl.href);
        if (opts.push === false) window.history.replaceState({ radio: 1 }, "", address);
        else window.history.pushState({ radio: 1 }, "", address);
        initPage();
        pollServer().then(pollSong);
        const message = doc.querySelector("[data-toast]")?.dataset.toastMessage;
        if (message) toast(message);
      })
      .catch(() => fallback(href));
  }

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download") || "noSoft" in link.dataset) return;
    const url = new URL(link.href, window.location.href);
    if (!inApp(url)) return;
    if (url.hash && url.pathname === window.location.pathname && url.search === window.location.search) return;
    event.preventDefault();
    navigate(url.href);
  });

  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || "noSoft" in form.dataset) return;
    const action = new URL(form.getAttribute("action") || window.location.href, window.location.href);
    if (!inApp(action)) return;
    event.preventDefault();
    const data = new FormData(form);
    const submitter = event.submitter;
    if (submitter && submitter.name) data.append(submitter.name, submitter.value);
    const params = new URLSearchParams(data);
    if ((form.getAttribute("method") || "get").toLowerCase() === "post") {
      navigate(action.href, { method: "POST", body: params });
    } else {
      action.search = params.toString();
      navigate(action.href);
    }
  });

  window.addEventListener("popstate", () => {
    if (inApp(new URL(window.location.href))) navigate(window.location.href, { push: false });
  });

  try {
    const pending = sessionStorage.getItem("radio-toast");
    if (pending) {
      sessionStorage.removeItem("radio-toast");
      toast(pending);
    }
  } catch {
    /* ignore */
  }

  initPage();
  pollServer().then(pollSong);
})();
