/* StonePi Car Thing panel. Plain ES2017 for the firmware's Chromium (~70): no optional
   chaining, no ??, no frameworks. Hardware arrives as keys/wheel (pything's contract):
   1-4 presets, M, Backspace = back, Enter = dial press, horizontal scroll = dial. */
(function () {
  "use strict";

  var cfg = JSON.parse(document.getElementById("cfg").textContent);
  var body = document.body;
  var view = document.getElementById("view");
  var saver = document.getElementById("saver");
  var toastEl = document.getElementById("toast");
  var offlineEl = document.getElementById("offline");

  var KEYS = {
    "1": "preset1", "2": "preset2", "3": "preset3", "4": "preset4",
    "m": "m", "M": "m", "Backspace": "back", "Enter": "dial_press",
    // Desktop/dev conveniences
    "Escape": "back", "ArrowRight": "dial_cw", "ArrowDown": "dial_cw", "ArrowLeft": "dial_ccw", "ArrowUp": "dial_ccw"
  };
  var LONG_MS = 600;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

  // ── Time: the device has no reliable clock, so follow the Pi's (from /tick) ──
  var clockOffset = cfg.now_ms - Date.now();
  var tzMin = cfg.tz_min;
  function piNow() { return new Date(Date.now() + clockOffset + tzMin * 60000); } // read with getUTC*

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hourParts(d) {
    var h = d.getUTCHours();
    if (cfg.clock.hour_format === "12") {
      return { h: String(h % 12 || 12), ampm: h < 12 ? "AM" : "PM" };
    }
    return { h: pad(h), ampm: "" };
  }
  function smallTime(d) {
    var p = hourParts(d);
    return p.h + ":" + pad(d.getUTCMinutes()) + (p.ampm ? " " + p.ampm : "");
  }
  function dateText(d) {
    var day = d.getUTCDay(), date = d.getUTCDate(), mon = d.getUTCMonth();
    switch (cfg.clock.date_format) {
      case "dddd d MMMM": return DAYS[day] + " " + date + " " + MONTHS[mon];
      case "d/M": return date + "/" + (mon + 1);
      case "yyyy-MM-dd": return d.getUTCFullYear() + "-" + pad(mon + 1) + "-" + pad(date);
      default: return DAYS[day].slice(0, 3) + " " + date + " " + MONTHS[mon].slice(0, 3);
    }
  }

  // ── Clock faces (built once per element, then only text/attributes change) ──
  var SEG = { a: [20, 4, 60, 14], b: [82, 20, 14, 66], c: [82, 94, 14, 66], d: [20, 162, 60, 14], e: [4, 94, 14, 66], f: [4, 20, 14, 66], g: [20, 83, 60, 14] };
  var DIGITS = ["abcdef", "bc", "abged", "abgcd", "fgbc", "afgcd", "afgedc", "abc", "abcdefg", "abcdfg"];
  var HOURS_W = ["TWELVE", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN"];
  var MIN_W = { 5: "FIVE PAST", 10: "TEN PAST", 15: "QUARTER PAST", 20: "TWENTY PAST", 25: "TWENTY-FIVE PAST", 30: "HALF PAST",
    35: "TWENTY-FIVE TO", 40: "TWENTY TO", 45: "QUARTER TO", 50: "TEN TO", 55: "FIVE TO" };

  function segDigit(x, scale, cls) {
    var out = "";
    for (var k in SEG) {
      var r = SEG[k];
      out += '<rect class="seg-off ' + cls + '" data-s="' + k + '" x="' + (x + r[0] * scale) + '" y="' + (r[1] * scale + (1 - scale) * 180) +
        '" width="' + r[2] * scale + '" height="' + r[3] * scale + '" rx="' + 6 * scale + '"/>';
    }
    return out;
  }
  function buildSegment(el) {
    var secs = cfg.clock.seconds;
    var x = 0, svg = "";
    svg += segDigit(x, 1, "d0"); x += 112;
    svg += segDigit(x, 1, "d1"); x += 118;
    svg += '<rect class="seg-dot" x="' + x + '" y="48" width="16" height="16" rx="8"/><rect class="seg-dot" x="' + x + '" y="116" width="16" height="16" rx="8"/>';
    x += 34;
    svg += segDigit(x, 1, "d2"); x += 112;
    svg += segDigit(x, 1, "d3"); x += 112;
    if (secs) { x += 14; svg += segDigit(x, 0.45, "d4"); x += 52; svg += segDigit(x, 0.45, "d5"); x += 50; }
    el.innerHTML = '<svg viewBox="-4 0 ' + (x + 8) + ' 184" preserveAspectRatio="xMidYMid meet">' + svg + "</svg>";
  }
  function paintSegment(el, digits) {
    for (var i = 0; i < digits.length; i++) {
      var rects = el.querySelectorAll("rect.d" + i);
      var on = digits.charAt(i) === " " ? "" : DIGITS[+digits.charAt(i)];
      for (var j = 0; j < rects.length; j++) {
        rects[j].setAttribute("class", (on.indexOf(rects[j].getAttribute("data-s")) >= 0 ? "seg-on" : "seg-off") + " d" + i);
      }
    }
  }
  function buildAnalogue(el) {
    var ticks = "";
    for (var i = 0; i < 60; i++) {
      var major = i % 5 === 0;
      ticks += '<line class="an-tick' + (major ? "" : " minor") + '" x1="100" y1="' + (major ? 10 : 12) + '" x2="100" y2="' + (major ? 22 : 16) +
        '" transform="rotate(' + i * 6 + ' 100 100)"/>';
    }
    el.innerHTML = '<svg viewBox="0 0 200 200"><circle class="an-face" cx="100" cy="100" r="96"/>' + ticks +
      '<line class="an-hour" x1="100" y1="112" x2="100" y2="52"/><line class="an-min" x1="100" y1="116" x2="100" y2="26"/>' +
      (cfg.clock.seconds ? '<line class="an-sec" x1="100" y1="122" x2="100" y2="18"/>' : "") +
      '<circle class="an-hub" cx="100" cy="100" r="6"/></svg>';
  }
  function words(d) {
    var h = d.getUTCHours(), m = Math.round(d.getUTCMinutes() / 5) * 5;
    if (m === 60) { m = 0; h += 1; }
    if (m > 30) h += 1;
    var hw = HOURS_W[h % 12];
    return '<span class="soft">IT’S</span> ' + (m === 0 ? hw + " O’CLOCK" : MIN_W[m] + " " + hw);
  }

  function buildClock(el) {
    var face = el.getAttribute("data-face");
    el.setAttribute("data-built", face);
    if (face === "segment") return buildSegment(el);
    if (face === "analogue") return buildAnalogue(el);
    if (face === "words") return;
    if (face === "flip") {
      el.innerHTML = '<span class="flip-card hh"></span><span class="flip-sep">:</span><span class="flip-card mm"></span>' +
        (cfg.clock.seconds ? '<span class="ss"></span>' : "") + '<span class="ampm"></span>';
      return;
    }
    if (face === "stacked") {
      el.innerHTML = '<span class="hh"></span><span class="mm"></span>';
      return;
    }
    el.innerHTML = '<span class="hh"></span><span class="colon">:</span><span class="mm"></span>' +
      (cfg.clock.seconds ? '<span class="ss"></span>' : "") + '<span class="ampm"></span>';
  }
  function setText(el, sel, text) {
    var node = el.querySelector(sel);
    if (node && node.textContent !== text) node.textContent = text;
  }
  function paintClock(el, d) {
    if (el.getAttribute("data-built") !== el.getAttribute("data-face")) buildClock(el);
    var face = el.getAttribute("data-face");
    var p = hourParts(d), mm = pad(d.getUTCMinutes()), ss = pad(d.getUTCSeconds());
    if (face === "segment") {
      var hh = p.h.length < 2 ? " " + p.h : p.h;
      return paintSegment(el, hh + mm + (cfg.clock.seconds ? ss : ""));
    }
    if (face === "analogue") {
      var s = d.getUTCSeconds(), m = d.getUTCMinutes() + s / 60, h = (d.getUTCHours() % 12) + m / 60;
      el.querySelector(".an-hour").setAttribute("transform", "rotate(" + h * 30 + " 100 100)");
      el.querySelector(".an-min").setAttribute("transform", "rotate(" + m * 6 + " 100 100)");
      var sec = el.querySelector(".an-sec");
      if (sec) sec.setAttribute("transform", "rotate(" + s * 6 + " 100 100)");
      return;
    }
    if (face === "words") {
      var w = words(d);
      if (el.innerHTML !== w) el.innerHTML = w;
      return;
    }
    setText(el, ".hh", p.h);
    setText(el, ".mm", mm);
    if (cfg.clock.seconds) setText(el, ".ss", ss);
    setText(el, ".ampm", p.ampm);
  }

  var lastMinute = -1;
  function paintTime(force) {
    var d = piNow();
    var minute = d.getUTCMinutes();
    if (!force && !cfg.clock.seconds && minute === lastMinute) return;
    lastMinute = minute;
    var i, nodes = document.querySelectorAll("[data-clock-small]");
    for (i = 0; i < nodes.length; i++) nodes[i].textContent = smallTime(d);
    nodes = document.querySelectorAll("[data-date]");
    for (i = 0; i < nodes.length; i++) nodes[i].textContent = dateText(d);
    // Screensaver clock, or a Clock (or Clock & weather) widget on the page on screen.
    nodes = (saver.hidden ? view : saver).querySelectorAll("[data-clock]");
    for (i = 0; i < nodes.length; i++) paintClock(nodes[i], d);
  }

  // ── Navigation + focus ─────────────────────────────────────────────────
  var pageCount = cfg.pages.length;
  var pageIndex = cfg.page || 0;
  var pageShownAt = Date.now();
  function pageUrl(i) { return "v/home?page=" + (i + 1); }
  var stack = [];
  var current = pageUrl(pageIndex);
  var focusIndex = 0;
  var pinDigits = "";
  var loading = false;

  function focusables() {
    var all = view.querySelectorAll("[data-focus]"), out = [];
    for (var i = 0; i < all.length; i++) if (all[i].offsetParent !== null) out.push(all[i]);
    return out;
  }
  function setFocus(i) {
    var items = focusables();
    if (!items.length) { focusIndex = 0; return; }
    focusIndex = (i + items.length) % items.length;
    for (var j = 0; j < items.length; j++) items[j].classList.toggle("is-focus", j === focusIndex);
    items[focusIndex].scrollIntoView({ block: "nearest" });
  }
  function screenKind() {
    var s = view.querySelector("[data-screen]");
    return s ? s.getAttribute("data-screen") : "";
  }
  function currentApp() {
    var s = view.querySelector("[data-app]");
    return s && screenKind() !== "home" ? s.getAttribute("data-app") : "";
  }

  // quiet: a background refresh (new feed data, link back), which leaves an auto-hidden strip hidden.
  function load(url, push, focus, quiet) {
    loading = true;
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 403) { location.reload(); return null; }
      return r.ok ? r.text() : null;
    }).then(function (html) {
      loading = false;
      if (html === null) { toast("Couldn’t load that."); return; }
      if (push && current !== url) stack.push({ url: current, focus: focusIndex });
      var stripWasHidden = quiet && url === current && !!view.querySelector("[data-strip-auto].is-hidden");
      current = url;
      view.innerHTML = html;
      pinDigits = "";
      setFocus(focus || 0);
      paintTime(true);
      npLook(null);
      if (screenKind() === "restarting") rsWatch();
      var strip = stripWasHidden && view.querySelector("[data-strip-auto]");
      if (strip) strip.classList.add("is-hidden");
      else showStrip();
    }).catch(function (err) { loading = false; fail(url, err); });
  }

  // Auto-hide header: shown (with the page below it) when you arrive on a page or touch, turn
  // or press anything, then hidden after the page's hide_after_s so the page has the screen.
  var stripTimer = null;
  function showStrip() {
    var strip = view.querySelector("[data-strip-auto]");
    clearTimeout(stripTimer);
    if (!strip) return;
    strip.classList.remove("is-hidden");
    stripTimer = setTimeout(function () { strip.classList.add("is-hidden"); }, Number(strip.getAttribute("data-strip-auto")) * 1000);
  }
  // A touch, press or turn: the strip comes back, except over Now Playing or a full web page,
  // where you touch the screen all the time (only arriving there shows it).
  function stripOnInput() {
    if (view.querySelector('[data-screen="music"], [data-np-full], .lay-web')) return;
    showStrip();
  }
  // Home = the page you were on; the "home" action goes to page 1.
  function goHome() { stack = []; pageShownAt = Date.now(); return load(pageUrl(pageIndex), false); }
  function goPage(i) {
    if (!pageCount) return;
    i = (i + pageCount) % pageCount;
    if (i === pageIndex && screenKind() === "home" && !stack.length) return;
    pageIndex = i;
    if (cfg.preview && window.parent !== window) {
      // Keep the editor's page picker in step with the preview.
      window.parent.postMessage({ source: "stonepi-carthing-panel", type: "page", page: i }, location.origin);
    }
    return goHome();
  }
  function goBack() {
    if (stack.length) {
      var prev = stack.pop();
      return load(prev.url, false, prev.focus);
    }
    if (current !== pageUrl(pageIndex)) return goHome();
  }
  function openApp(id) {
    var idx = 0;
    if (screenKind() === "home") {
      var items = focusables();
      for (var j = 0; j < items.length; j++) if (items[j].getAttribute("data-app") === id) { idx = j; break; }
    }
    stack = [{ url: pageUrl(pageIndex), focus: idx }];
    return load(id === "music" ? "v/music" : "v/app/" + id, false);
  }
  // The weather screen (U14): from a widget tap (data-go) or the Open Weather action.
  function openWeather() {
    if (current === "v/weather") return;
    stack = [{ url: pageUrl(pageIndex), focus: screenKind() === "home" ? focusIndex : 0 }];
    return load("v/weather", false);
  }
  function cycleApp(step) {
    var apps = cfg.apps;
    if (!apps.length) return;
    var i = apps.indexOf(currentApp());
    i = i < 0 ? (step > 0 ? 0 : apps.length - 1) : (i + step + apps.length) % apps.length;
    openApp(apps[i]);
  }
  function move(step) {
    var scroller = view.querySelector("[data-scroller]");
    if (screenKind() === "item" && scroller && scroller.scrollHeight > scroller.clientHeight) {
      scroller.scrollTop += step * 90;
      return;
    }
    setFocus(focusIndex + step);
  }
  function select() {
    var items = focusables();
    if (items.length) activate(items[focusIndex] || items[0]);
  }
  function activate(el) {
    var go = el.getAttribute("data-go");
    var act = el.getAttribute("data-act");
    var key = el.getAttribute("data-key");
    if (go) return load(go, true);
    if (key) return pinKey(key);
    if (el.hasAttribute("data-np-more")) return npSheet();
    var mus = el.getAttribute("data-music");
    if (mus) return musicCmd(mus);
    if (el.hasAttribute("data-device")) {
      return musicCmd("transfer", el.getAttribute("data-device"), "Playing on " + el.getAttribute("data-device-name") + ".").then(afterPick);
    }
    if (el.hasAttribute("data-preset")) return musicCmd("preset", +el.getAttribute("data-preset")).then(afterPick);
    if (act === "restart") return load("v/restart?unit=" + encodeURIComponent(el.getAttribute("data-unit")), true);
    if (act === "restart-now") return postRestart(el.getAttribute("data-unit"), "");
    if (act === "back") return goBack();
  }

  // ── PIN + restart ──────────────────────────────────────────────────────
  function paintPin() {
    var dots = view.querySelectorAll(".pin-dots span");
    for (var i = 0; i < dots.length; i++) dots[i].classList.toggle("on", i < pinDigits.length);
  }
  function pinKey(k) {
    var box = view.querySelector("[data-pin]");
    if (!box) return;
    var len = +box.getAttribute("data-length");
    if (k === "del") pinDigits = pinDigits.slice(0, -1);
    else if (k === "ok") { if (pinDigits.length === len) postRestart(box.getAttribute("data-unit"), pinDigits); return; }
    else if (pinDigits.length < len) pinDigits += k;
    paintPin();
    if (pinDigits.length === len) postRestart(box.getAttribute("data-unit"), pinDigits);
  }
  function postRestart(unit, pin) {
    return fetch("restart", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unit: unit, pin: pin })
    }).then(function (r) { return r.json(); }).then(function (res) {
      // Restarted: the progress screen takes the confirm's place (Back returns to the service).
      if (res.ok) { load("v/restarting?unit=" + encodeURIComponent(unit), false); return; }
      var msg = view.querySelector("[data-pin-msg]");
      if (msg) { msg.textContent = res.message || "That didn’t work."; msg.classList.add("bad"); pinDigits = ""; paintPin(); }
      else toast(res.message || "That didn’t work.");
    }).catch(function (err) { fail("restart", err); });
  }

  // After a restart: ask the Pi every 1.5 s until the service runs again (stopped → starting →
  // running), then offer the way back. Gives up quietly after 45 s.
  var rsTimer = null;
  function rsWatch() {
    var box = view.querySelector("[data-restarting]");
    clearTimeout(rsTimer);
    if (!box) return;
    var unit = box.getAttribute("data-restarting"), started = Date.now();
    function step(name, cls) {
      var el = box.querySelector('[data-rs-step="' + name + '"]');
      if (el) el.className = "rs-step " + cls;
    }
    function finish(title, msg) {
      box.querySelector("[data-rs-title]").textContent = title;
      box.querySelector("[data-rs-msg]").textContent = msg;
      var back = box.querySelector("[data-rs-back]");
      back.hidden = false;
      var items = focusables();
      for (var i = 0; i < items.length; i++) if (items[i] === back) setFocus(i);
    }
    function ask() {
      if (!view.contains(box)) return; // left the screen
      fetch("unit-state?unit=" + encodeURIComponent(unit), { credentials: "same-origin" }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (st) {
        if (st && st.ok && st.state === "active" && (!st.sub || st.sub === "running")) {
          step("starting", "is-done");
          step("running", "is-done");
          finish(box.querySelector("[data-rs-title]").getAttribute("data-done"), "Running again.");
          return;
        }
        if (st && st.state === "failed") {
          step("running", "is-bad");
          finish("It didn't start", "Open the service again to see its last log lines.");
          return;
        }
        if (Date.now() - started > 45000) {
          finish(box.querySelector("[data-rs-title]").getAttribute("data-slow"), "Check it again from System in a moment.");
          return;
        }
        rsTimer = setTimeout(ask, 1500);
      }).catch(function () { rsTimer = setTimeout(ask, 1500); }); // Dashboard itself may be restarting
    }
    rsTimer = setTimeout(ask, 800);
  }

  // ── Actions ────────────────────────────────────────────────────────────
  function allowed(action) { return cfg.allowed.indexOf(action) >= 0; }
  function perform(action) {
    if (!action || !allowed(action)) return;
    if (action === "open:weather") return openWeather();
    if (action.indexOf("music:preset:") === 0) return musicCmd("preset", +action.slice(13));
    if (action === "music:vol_up") return volStep(1);
    if (action === "music:vol_down") return volStep(-1);
    if (action.indexOf("music:") === 0) return musicCmd(action.slice(6));
    if (action.indexOf("open:") === 0) return openApp(action.slice(5));
    if (action.indexOf("page:") === 0) {
      var n = +action.slice(5);
      return n >= 1 && n <= pageCount ? goPage(n - 1) : undefined;
    }
    switch (action) {
      case "home": return goPage(0);
      case "next_page": return goPage(pageIndex + 1);
      case "prev_page": return goPage(pageIndex - 1);
      case "back": return goBack();
      case "next": return move(1);
      case "prev": return move(-1);
      case "select": return select();
      case "next_app": return cycleApp(1);
      case "prev_app": return cycleApp(-1);
      case "screensaver": return showSaver();
      case "sleep": return sleep();
      case "brightness_up": bright = Math.min(1, bright + 0.2); return applyLevel(true);
      case "brightness_down": bright = Math.max(0.2, bright - 0.2); return applyLevel(true);
      case "restart": return openApp("system");
    }
  }

  // ── Music (F7): commands, dial volume, Now Playing updates ────────────
  var npTimer = null, npFresh = null;
  function npRoot() { return mode === "saver" ? saver : view; }
  function musicCmd(cmd, arg, okText) {
    if (cmd === "play_pause") {
      // Show the new state at once; the next read confirms it.
      var btns = npRoot().querySelectorAll('[data-music="play_pause"]');
      for (var i = 0; i < btns.length; i++) btns[i].classList.toggle("is-playing");
    }
    return fetch("music/cmd", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cmd: cmd, arg: arg === undefined ? null : arg })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (!res.ok) { toast(res.message || "That didn’t work."); }
      else if (res.message || okText) toast(res.message || okText);
      npSoon(700);
      return res;
    }).catch(function (err) { fail("music " + cmd, err); return { ok: false }; });
  }
  // A speaker or preset picked from its list: back to Now Playing.
  function afterPick(res) { if (res && res.ok && screenKind() === "music-list") goBack(); }

  // Dial clicks add up for ~300 ms and go out as one volume change.
  var volClicks = 0, volTimer = null;
  function volStep(dir) {
    if (!allowed(dir > 0 ? "music:vol_up" : "music:vol_down")) return;
    volClicks += dir;
    var vol = npRoot().querySelector("[data-np-vol]");
    var bar = vol && vol.querySelector("i");
    if (bar) {
      var now = parseFloat(bar.style.width) || 0;
      var level = Math.max(0, Math.min(100, now + dir * cfg.music.step));
      bar.style.width = level + "%";
      var n = vol.querySelector("[data-np-vol-n]");
      if (n) n.textContent = level;
      vol.classList.add("is-on");
      clearTimeout(volStep.shown);
      volStep.shown = setTimeout(function () { vol.classList.remove("is-on"); }, 1500);
    }
    clearTimeout(volTimer);
    volTimer = setTimeout(function () {
      var clicks = volClicks;
      volClicks = 0;
      if (clicks) musicCmd("vol", clicks * cfg.music.step);
    }, 300);
  }
  // Now Playing with the dial on volume: only when the speaker takes remote volume and the
  // volume actions are allowed; otherwise the dial keeps its mapping.
  // Now Playing on screen: the Music screen, or a page holding the full-page music widget.
  function npShown() {
    var kind = screenKind();
    return kind === "music" || (kind === "home" && !!view.querySelector("[data-np-full]"));
  }
  function dialIsVolume(id) {
    if ((id !== "dial_cw" && id !== "dial_ccw") || !cfg.music.dial_volume || !npShown()) return false;
    var vol = view.querySelector("[data-np-vol]");
    return !!vol && !vol.hidden && allowed(id === "dial_cw" ? "music:vol_up" : "music:vol_down");
  }

  function fmt(ms) { var s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ":" + pad(s % 60); }
  function setAll(root, sel, text) {
    var nodes = root.querySelectorAll(sel);
    for (var i = 0; i < nodes.length; i++) if (nodes[i].textContent !== text) nodes[i].textContent = text;
  }
  function paintNp(st) {
    var root = npRoot();
    if (!root.querySelector("[data-np]") && !root.querySelector("[data-saver-music]")) return;
    var i, nodes;
    setAll(root, "[data-np-track]", st.track || (st.connected ? "Nothing playing" : "Music"));
    setAll(root, "[data-np-artist]", st.track ? st.artist : (st.error || "Start something with a preset, or pick a speaker."));
    setAll(root, "[data-np-album]", st.album || "");
    setAll(root, "[data-np-device]", st.device ? st.device.name : "");
    nodes = root.querySelectorAll("[data-np-art]");
    for (i = 0; i < nodes.length; i++) {
      if (st.art && nodes[i].getAttribute("src") !== st.art) nodes[i].setAttribute("src", st.art);
      nodes[i].hidden = !st.art;
    }
    nodes = root.querySelectorAll("[data-np-art-empty]");
    for (i = 0; i < nodes.length; i++) nodes[i].hidden = !!st.art;
    nodes = root.querySelectorAll("[data-np-bar]");
    for (i = 0; i < nodes.length; i++) {
      nodes[i].setAttribute("data-progress", st.progress_ms);
      nodes[i].setAttribute("data-duration", st.duration_ms);
      nodes[i].setAttribute("data-at", st.at_ms);
      nodes[i].setAttribute("data-playing", st.playing ? "1" : "0");
    }
    nodes = root.querySelectorAll('[data-music="play_pause"]');
    for (i = 0; i < nodes.length; i++) nodes[i].classList.toggle("is-playing", !!st.playing);
    nodes = root.querySelectorAll('[data-music="shuffle"]');
    for (i = 0; i < nodes.length; i++) nodes[i].classList.toggle("on", !!st.shuffle);
    nodes = root.querySelectorAll('[data-music="save"]');
    for (i = 0; i < nodes.length; i++) nodes[i].classList.toggle("on", st.liked === true);
    nodes = root.querySelectorAll(".np2-where");
    for (i = 0; i < nodes.length; i++) nodes[i].hidden = !st.device;
    nodes = root.querySelectorAll('[data-music="repeat"]');
    for (i = 0; i < nodes.length; i++) { nodes[i].classList.toggle("on", st.repeat !== "off"); nodes[i].setAttribute("data-repeat", st.repeat); }
    var d = st.device;
    nodes = root.querySelectorAll("[data-np-vol]");
    for (i = 0; i < nodes.length; i++) {
      var hasVol = !!(d && d.supports_volume && d.volume !== null);
      nodes[i].hidden = !hasVol;
      if (hasVol && !volClicks) {
        nodes[i].querySelector("i").style.width = d.volume + "%";
        var num = nodes[i].querySelector("[data-np-vol-n]");
        if (num) num.textContent = d.volume;
      }
    }
    npPlaying = !!st.playing;
    npLook(st);
    paintBars();
  }
  // ── Now Playing look: colours from the artwork, blurred background, controls on touch ──
  // Once per track: the artwork (same origin, art/<key>) is drawn into a 24×24 canvas, its
  // most common colour becomes the background and its liveliest bright colour the accent.
  // Blurred artwork is the cover drawn tiny and stretched (the browser's smoothing does the
  // blur), so the device never runs a CSS blur. Plain ES5 for the firmware's Chromium.
  var npTouchedAt = 0, npPlaying = false;
  function hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, h = 0, s = 0, d = max - min;
    if (d) {
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h /= 6;
    }
    return [h, s, l];
  }
  function hslCss(h, s, l, a) {
    return "hsla(" + Math.round(h * 360) + "," + Math.round(s * 100) + "%," + Math.round(l * 100) + "%," + (a === undefined ? 1 : a) + ")";
  }
  function artColours(img) {
    var c = document.createElement("canvas");
    c.width = 24; c.height = 24;
    var x = c.getContext("2d");
    x.drawImage(img, 0, 0, 24, 24);
    var px;
    try { px = x.getImageData(0, 0, 24, 24).data; } catch (e) { return null; }
    var buckets = {}, i, key;
    for (i = 0; i < px.length; i += 4) {
      key = (px[i] >> 5) + "," + (px[i + 1] >> 5) + "," + (px[i + 2] >> 5);
      var b = buckets[key] || (buckets[key] = { n: 0, r: 0, g: 0, b: 0 });
      b.n += 1; b.r += px[i]; b.g += px[i + 1]; b.b += px[i + 2];
    }
    var main = null, lively = null, best = -1;
    for (key in buckets) {
      var k = buckets[key], col = hsl(k.r / k.n, k.g / k.n, k.b / k.n);
      k.h = col;
      if (!main || k.n > main.n) main = k;
      var score = col[1] * (0.4 + Math.min(col[2], 0.6)) * Math.sqrt(k.n);
      if (col[1] > 0.25 && col[2] > 0.15 && col[2] < 0.92 && score > best) { best = score; lively = k; }
    }
    var h = main.h[0], s = main.h[1];
    return {
      bg: hslCss(h, s > 0.08 ? Math.max(Math.min(s, 0.6), 0.25) : 0, 0.32),
      bg2: hslCss(h, Math.min(s, 0.5), 0.09),
      accent: lively ? [lively.h[0], Math.max(lively.h[1], 0.55), Math.max(0.58, Math.min(lively.h[2], 0.7))] : null
    };
  }
  function paintBlur(el, img) {
    var canvas = el.querySelector("[data-np-blur]");
    if (!canvas) return;
    var tiny = document.createElement("canvas");
    tiny.width = 16; tiny.height = 10;
    var w = img.naturalWidth, h = img.naturalHeight, ch = w * 10 / 16;
    tiny.getContext("2d").drawImage(img, 0, Math.max(0, (h - ch) / 2), w, Math.min(h, ch), 0, 0, 16, 10);
    var x = canvas.getContext("2d");
    x.imageSmoothingEnabled = true;
    x.drawImage(tiny, 0, 0, canvas.width, canvas.height);
    x.fillStyle = "rgba(0,0,0,0.48)";
    x.fillRect(0, 0, canvas.width, canvas.height);
  }
  function applyArt(el, img) {
    var mine = el.getAttribute("data-accent-mode") === "art";
    var cols = img && img.naturalWidth ? artColours(img) : null;
    var acc = cols && mine && cols.accent ? cols.accent : null;
    var accent = acc ? hslCss(acc[0], acc[1], acc[2]) : el.getAttribute("data-accent");
    var st = el.style;
    st.setProperty("--np-accent", accent);
    st.setProperty("--np-bg", cols ? cols.bg : "#1F2326");
    st.setProperty("--np-bg2", cols ? cols.bg2 : "#0B0D0E");
    // Text on the accent: dark on a light accent, white otherwise (rough luminance).
    var light = acc ? acc[2] > 0.62 : true;
    st.setProperty("--np-on-accent", light ? "#0B0D0E" : "#FFFFFF");
    st.setProperty("--np-glow", acc ? hslCss(acc[0], acc[1], acc[2], 0.5) : "rgba(199,187,168,0.45)");
    st.setProperty("--np-glow2", acc ? hslCss(acc[0], acc[1], acc[2], 0.14) : "rgba(199,187,168,0.12)");
    if (img && img.naturalWidth && el.classList.contains("bg-blur")) paintBlur(el, img);
  }
  function npLook(st) {
    var looks = npRoot().querySelectorAll("[data-np-look]");
    for (var i = 0; i < looks.length; i++) {
      var el = looks[i];
      if (st) el.classList.toggle("idle", !st.track && el.classList.contains("np2"));
      else {
        // Just rendered: the play state is in the markup until the next read.
        var bar = el.querySelector("[data-np-bar]");
        if (bar) npPlaying = bar.getAttribute("data-playing") === "1";
      }
      var img = el.querySelector("[data-np-art]");
      var src = img && !img.hidden ? img.getAttribute("src") : "";
      if (el.getAttribute("data-art-done") !== src) {
        el.setAttribute("data-art-done", src);
        if (!src) applyArt(el, null);
        else if (img.complete && img.naturalWidth) applyArt(el, img);
        else img.onload = (function (e, im) { return function () { applyArt(e, im); }; })(el, img);
      }
      // The control row: always, never, or for 4 s after a touch, press or turn (and while paused).
      var c = el.getAttribute("data-controls");
      var touched = Date.now() - npTouchedAt < 4000;
      el.classList.toggle("ctl-shown", c === "always" || (c === "touch" && (touched || !npPlaying)));
    }
  }
  function npTouched() {
    if (!npShown()) return;
    npTouchedAt = Date.now();
    npLook(null);
    clearTimeout(npTouched.timer);
    npTouched.timer = setTimeout(function () { npLook(null); }, 4100);
  }
  function npSheet(open) {
    var sheet = view.querySelector("[data-np-sheet]");
    if (!sheet) return;
    sheet.hidden = open === undefined ? !sheet.hidden : !open;
    if (!sheet.hidden) {
      var first = sheet.querySelector("[data-focus]");
      var items = focusables();
      for (var i = 0; i < items.length; i++) if (items[i] === first) setFocus(i);
    }
  }
  // Seeking: tap the progress line, or drag along it (never a swipe to another page).
  function seekRatio(line, x) {
    var r = line.getBoundingClientRect();
    return r.width ? Math.max(0, Math.min(1, (x - r.left) / r.width)) : 0;
  }
  function seekPreview(seek, x) {
    var bar = seek.closest("[data-np-bar]"), dur = +bar.getAttribute("data-duration");
    var ratio = seekRatio(seek.querySelector(".np-line"), x);
    seek.querySelector(".np-line i").style.width = Math.round(ratio * 1000) / 10 + "%";
    var p = bar.querySelector("[data-np-pos]");
    if (p && dur) p.textContent = fmt(ratio * dur);
    return Math.round(ratio * dur);
  }
  function seekTo(seek, x) {
    var bar = seek.closest("[data-np-bar]");
    var ms = seekPreview(seek, x);
    if (!+bar.getAttribute("data-duration")) return;
    bar.setAttribute("data-progress", ms);
    bar.setAttribute("data-at", Date.now() + clockOffset);
    musicCmd("seek", ms);
  }

  // Progress bars move every second between reads (the Pi's clock, not the device's).
  function paintBars() {
    var nodes = npRoot().querySelectorAll("[data-np-bar]");
    var now = Date.now() + clockOffset;
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i], dur = +el.getAttribute("data-duration"), pos = +el.getAttribute("data-progress");
      if (el.getAttribute("data-playing") === "1") pos += now - (+el.getAttribute("data-at"));
      pos = dur ? Math.min(pos, dur) : 0;
      var line = el.querySelector(".np-line i");
      var width = (dur ? Math.round(pos / dur * 1000) / 10 : 0) + "%";
      if (line && line.style.width !== width) line.style.width = width;
      var p = el.querySelector("[data-np-pos]");
      if (p) { var t = dur ? fmt(pos) : ""; if (p.textContent !== t) p.textContent = t; }
      var dEl = el.querySelector("[data-np-dur]");
      if (dEl) { var dt = dur ? fmt(dur) : ""; if (dEl.textContent !== dt) dEl.textContent = dt; }
    }
  }
  function npRead() {
    return fetch("np?src=screen", { credentials: "same-origin" }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (st) { if (st && st.ok) paintNp(st); }).catch(function () {});
  }
  // Poll every 3 s while Now Playing is on screen; one extra read soon after a command.
  function npSoon(ms) {
    clearTimeout(npFresh);
    npFresh = setTimeout(function () { if (npShown() || screenKind() === "home") npRead(); }, ms);
  }
  function npPoll() {
    clearTimeout(npTimer);
    npTimer = setTimeout(function () {
      if (mode === "active" && npShown()) npRead();
      npPoll();
    }, 3000);
  }
  npPoll();

  // ── Idle, screensaver, dimming ─────────────────────────────────────────
  var mode = "active";
  var lastInput = Date.now();
  var loadedAt = Date.now();
  var bright = 1;
  var forceQuiet = false;
  var backlight = false;
  var asleep = false; // Sleep action: screensaver at its darkest until the next input
  var appliedKey = "";

  function mins(text) { var p = String(text).split(":"); return (+p[0]) * 60 + (+p[1]); }
  function inQuiet() {
    var q = cfg.idle.quiet_hours;
    if (forceQuiet) return true;
    if (!q.enabled) return false;
    var d = piNow(), now = d.getUTCHours() * 60 + d.getUTCMinutes(), from = mins(q.from), to = mins(q.to);
    return from <= to ? now >= from && now < to : now >= from || now < to;
  }
  function targetLevel() {
    if (mode === "active") return { level: Math.round(bright * 100), off: false, quiet: false };
    var quiet = inQuiet(), q = cfg.idle.quiet_hours;
    if (quiet && q.mode === "off") return { level: 0, off: true, quiet: true };
    // Sleep: backlight off where we control it, otherwise the dimmest of the idle levels.
    if (asleep) {
      if (backlight) return { level: 0, off: true, quiet: quiet };
      return { level: Math.min(cfg.idle.dim_level, quiet ? q.level : 100), off: false, quiet: quiet };
    }
    if (quiet) return { level: q.level, off: false, quiet: true };
    var idle = (Date.now() - lastInput) / 1000;
    var dimAt = cfg.idle.screensaver_after_s + cfg.idle.dim_after_s;
    if (cfg.idle.dim_after_s > 0 && idle >= dimAt) return { level: cfg.idle.dim_level, off: false, quiet: false };
    return { level: Math.round(bright * 100), off: false, quiet: false };
  }

  function hexScale(hex, f) {
    var n = parseInt(String(hex).slice(1), 16);
    if (isNaN(n)) return hex;
    var r = Math.round(((n >> 16) & 255) * f), g = Math.round(((n >> 8) & 255) * f), b = Math.round((n & 255) * f);
    return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
  }
  var DIM_BASE = { "--fg": "#F5F1E8", "--muted": "#9A958A", "--card": "#161A20", "--line": "#262B33", "--good": "#7FE0B0", "--bad": "#FF6B5E" };
  var paletteF = 1; // colour scale while dimmed without a backlight (animations follow it)
  // Clock colours on screen: the screensaver's own while any screensaver is up and its clock is
  // set apart from the widgets (U13): the Clock face, Matrix's time, the corner time on Logo and
  // Pipes. Otherwise the widgets'.
  function clockColors() {
    var own = cfg.clock.saver;
    if (mode === "saver" && own && own.separate) return { color: own.color, accent: own.accent };
    return { color: cfg.clock.color, accent: cfg.clock.accent };
  }
  function setPalette(f, quiet) {
    paletteF = f;
    var cc = clockColors();
    var clock = quiet && cfg.clock.quiet_color ? cfg.clock.quiet_color : cc.color;
    if (f >= 0.999 && !(quiet && cfg.clock.quiet_color)) {
      body.classList.remove("dim");
      for (var k in DIM_BASE) body.style.removeProperty(k);
      body.style.setProperty("--accent", cfg.look.accent);
      body.style.setProperty("--clock", cc.color);
      body.style.setProperty("--clock-accent", cc.accent);
      return;
    }
    body.classList.add("dim");
    for (var key in DIM_BASE) body.style.setProperty(key, hexScale(DIM_BASE[key], f));
    body.style.setProperty("--accent", hexScale(cfg.look.accent, f));
    body.style.setProperty("--clock", hexScale(clock, f));
    body.style.setProperty("--clock-accent", hexScale(cc.accent, f));
  }
  function applyLevel(force) {
    var t = targetLevel();
    var key = t.level + "|" + t.off + "|" + t.quiet + "|" + backlight;
    if (!force && key === appliedKey) return;
    appliedKey = key;
    body.classList.toggle("off", t.off);
    if (backlight) {
      // Real backlight; colours stay true (quiet colour still applies).
      setPalette(1, t.quiet);
      fetch("screen", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: t.off ? 0 : Math.max(1, t.level) }) }).catch(function () {});
    } else {
      setPalette(t.off ? 0 : Math.max(0.08, t.level / 100), t.quiet);
    }
    // Animated screensavers: full rate when bright, half when dimmed or quiet, none when off.
    var dimmed = t.quiet || t.level < Math.round(bright * 100);
    animFps = t.off ? 0 : dimmed ? 15 : 30;
    if (anim) anim.recolor();
    runAnim();
  }

  // ── Animated screensavers (style ≠ clock) ──────────────────────────────
  // Each style builds its state on the freshly rendered saver and returns frame(dt), which
  // draws one step, and recolor(), which repaints after a dim change. One timer drives
  // whichever is on screen.
  var SPEEDS = { slow: 0.55, normal: 1, fast: 1.7 };
  var anim = null, animFps = 30, animTimer = null, animLast = 0;

  // Bouncing logo: the StonePi mark drifting like the DVD logo. Category colours (dark theme
  // icon tints) when it recolours, else the mark's own sand / stone / stone-deep slabs.
  var LOGO_COLORS = ["#C7BBA8", "#5CC2A6", "#60A5FA", "#E3A653", "#B19CF0"];
  var LOGO_FLAT = ["#C7BBA8", "#9AA0A6", "#80868C"];
  var LOGO_SIZE = 120, LOGO_PX_S = 70, CORNER_PX = 10;
  var logoPos = null; // kept across re-renders so the mark never jumps
  function logoSaver(el) {
    var o = cfg.idle.saver.logo;
    var mark = el.querySelector("[data-lg-mark]");
    var slabs = mark.querySelectorAll("polygon");
    var maxX = 800 - LOGO_SIZE, maxY = 480 - LOGO_SIZE;
    var v = LOGO_PX_S * (SPEEDS[o.speed] || 1);
    if (!logoPos) {
      logoPos = { x: Math.random() * maxX, y: Math.random() * maxY,
        vx: (Math.random() < 0.5 ? -1 : 1) * v * 0.8, vy: (Math.random() < 0.5 ? -1 : 1) * v * 0.6, c: 0 };
    }
    var p = logoPos;
    function paint() {
      for (var i = 0; i < slabs.length; i++) {
        var c = o.recolor ? LOGO_COLORS[p.c % LOGO_COLORS.length] : LOGO_FLAT[i];
        slabs[i].setAttribute("fill", hexScale(c, paletteF));
      }
    }
    function place() { mark.style.transform = "translate(" + Math.round(p.x) + "px," + Math.round(p.y) + "px)"; }
    paint();
    place();
    return {
      frame: function (dt) {
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        var hitX = false, hitY = false;
        if (p.x <= 0 || p.x >= maxX) { p.x = Math.max(0, Math.min(maxX, p.x)); p.vx = -p.vx; hitX = true; }
        if (p.y <= 0 || p.y >= maxY) { p.y = Math.max(0, Math.min(maxY, p.y)); p.vy = -p.vy; hitY = true; }
        if (hitX || hitY) {
          var nearX = p.x <= CORNER_PX || p.x >= maxX - CORNER_PX, nearY = p.y <= CORNER_PX || p.y >= maxY - CORNER_PX;
          if (o.recolor) { p.c++; paint(); }
          if (o.corner && nearX && nearY) {
            mark.classList.remove("is-corner");
            void mark.offsetWidth; // restart the pulse
            mark.classList.add("is-corner");
          }
        }
        place();
      },
      recolor: paint
    };
  }

  // Matrix: code rain on a canvas, ~20 px glyphs in 40 columns. Each column falls at its own
  // pace; the trail fades by painting translucent black over the canvas each frame. That fade
  // stalls at a faint residue (8-bit rounding), so each head also wipes the cell MX_TRAIL rows
  // behind it, and falls MX_TRAIL rows past the bottom before starting again.
  var MX_GLYPHS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ:.=*+-<>|";
  var MX_SIZE = 20, MX_ROWS_S = 13, MX_FADE = 0.86, MX_TRAIL = 22;
  var MX_GAP = { light: 40, normal: 14, dense: 4 }; // rows a column rests before falling again
  function matrixSaver(el) {
    var o = cfg.idle.saver.matrix;
    var ctx = el.querySelector("[data-mx]").getContext("2d");
    var cols = Math.floor(800 / MX_SIZE), rows = Math.ceil(480 / MX_SIZE);
    var rate = MX_ROWS_S * (SPEEDS[o.speed] || 1), gap = MX_GAP[o.density] || MX_GAP.normal;
    var drops = [];
    for (var i = 0; i < cols; i++) drops.push({ y: -Math.random() * (rows + gap), pace: 0.7 + Math.random() * 0.6, row: -1, ch: "" });
    // The leading glyph is the colour washed towards white.
    var n = parseInt(o.color.slice(1), 16);
    var wash = function (c) { return Math.round(c + (255 - c) * 0.7); };
    var headBase = "#" + ((1 << 24) | (wash((n >> 16) & 255) << 16) | (wash((n >> 8) & 255) << 8) | wash(n & 255)).toString(16).slice(1);
    var bodyColor = "", headColor = "";
    function paint() {
      bodyColor = hexScale(o.color, paletteF);
      headColor = hexScale(headBase, paletteF);
    }
    function glyph() { return MX_GLYPHS.charAt(Math.floor(Math.random() * MX_GLYPHS.length)); }
    paint();
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, 800, 480);
    ctx.font = "bold " + (MX_SIZE - 2) + "px monospace";
    ctx.textBaseline = "top";
    return {
      frame: function (dt) {
        ctx.fillStyle = "rgba(0,0,0," + (1 - Math.pow(MX_FADE, dt * rate)).toFixed(3) + ")";
        ctx.fillRect(0, 0, 800, 480);
        for (var c = 0; c < cols; c++) {
          var d = drops[c], x = c * MX_SIZE;
          d.y += rate * d.pace * dt;
          var row = Math.floor(d.y);
          // Step through every row crossed, so a fast column leaves no gaps.
          for (var r = Math.max(d.row + 1, 0); r <= row; r++) {
            // The previous head turns body colour; a new glyph leads.
            if (d.row >= 0 && d.row < rows) { ctx.fillStyle = bodyColor; ctx.fillText(d.ch, x, d.row * MX_SIZE); }
            if (r - MX_TRAIL >= 0 && r - MX_TRAIL < rows) { ctx.fillStyle = "#000"; ctx.fillRect(x, (r - MX_TRAIL) * MX_SIZE, MX_SIZE, MX_SIZE); }
            if (r < rows) { d.ch = glyph(); ctx.fillStyle = headColor; ctx.fillText(d.ch, x, r * MX_SIZE); }
            d.row = r;
          }
          if (d.row >= rows + MX_TRAIL) { d.y = -Math.random() * gap; d.row = -1; d.pace = 0.7 + Math.random() * 0.6; }
        }
      },
      recolor: paint
    };
  }

  // 3D Pipes, after the Windows 98 screensaver: a pipe grows cell by cell through a 3D grid
  // seen in perspective, turning at random, until it boxes itself in; then the next one starts
  // in a new colour. Once the screen is busy it holds for a moment, clears and begins again.
  // Each step draws one shaded segment (and a joint at a turn) on top of what's there, so a
  // frame costs almost nothing. Steps are kept so a dim change can repaint them all.
  var PP_NX = 16, PP_NY = 10, PP_NZ = 10, PP_D = 12, PP_R = 0.27;
  var PP_SCALE = 760 * PP_D / PP_NX; // the front layer spans the screen
  var PP_STEPS_S = 8, PP_TURN = 0.28, PP_FILL = 0.42, PP_HOLD_S = 2.5, PP_BALL = 0.2;
  var PP_LEN_MIN = 30, PP_LEN_MAX = 70; // segments before a pipe ends anyway, so colours change
  var PP_COLORS = ["#E53935", "#43A047", "#1E88E5", "#FDD835", "#00ACC1", "#8E24AA", "#FB8C00", "#ECEFF1"];
  var PP_DIRS = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
  // A pipe colour darkened (f < 1) or washed towards white (toWhite), then dim-scaled.
  function ppMix(hex, f, toWhite) {
    var n = parseInt(String(hex).slice(1), 16);
    var ch = function (c) { return Math.round(toWhite ? c + (255 - c) * f : c * f); };
    return hexScale("#" + ((1 << 24) | (ch((n >> 16) & 255) << 16) | (ch((n >> 8) & 255) << 8) | ch(n & 255)).toString(16).slice(1), paletteF);
  }
  function pipesSaver(el) {
    var o = cfg.idle.saver.pipes;
    var ctx = el.querySelector("[data-pp]").getContext("2d");
    var rate = PP_STEPS_S * (SPEEDS[o.speed] || 1);
    var total = PP_NX * PP_NY * PP_NZ;
    var taken, used, ops, pipe, colorAt, acc = 0, hold = 0;
    function key(p) { return p[0] + "," + p[1] + "," + p[2]; }
    function inside(p) { return p[0] >= 0 && p[0] < PP_NX && p[1] >= 0 && p[1] < PP_NY && p[2] >= 0 && p[2] < PP_NZ; }
    function proj(p) {
      var s = PP_SCALE / (PP_D + p[2]);
      return { x: 400 + (p[0] - (PP_NX - 1) / 2) * s, y: 240 + (p[1] - (PP_NY - 1) / 2) * s, r: PP_R * s };
    }
    function drawJoint(op) {
      var c = proj(op.p), r = c.r * (op.ball ? 1.6 : 1);
      var g = ctx.createRadialGradient(c.x - r * 0.35, c.y - r * 0.35, r * 0.1, c.x, c.y, r);
      g.addColorStop(0, ppMix(op.color, 0.65, true));
      g.addColorStop(0.45, ppMix(op.color, 1));
      g.addColorStop(1, ppMix(op.color, 0.3));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(c.x, c.y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    function drawSeg(op) {
      var a = proj(op.a), b = proj(op.b);
      var dx = b.x - a.x, dy = b.y - a.y, len = Math.sqrt(dx * dx + dy * dy);
      // Seen end-on (straight into the screen): a disc.
      if (len < 1) return drawJoint({ p: op.a[2] < op.b[2] ? op.a : op.b, color: op.color, ball: false });
      var nx = -dy / len, ny = dx / len;
      if (nx + ny > 0) { nx = -nx; ny = -ny; } // lit edge towards the top left
      var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, mr = (a.r + b.r) / 2;
      var g = ctx.createLinearGradient(mx + nx * mr, my + ny * mr, mx - nx * mr, my - ny * mr);
      g.addColorStop(0, ppMix(op.color, 0.55));
      g.addColorStop(0.25, ppMix(op.color, 0.6, true));
      g.addColorStop(0.5, ppMix(op.color, 1));
      g.addColorStop(1, ppMix(op.color, 0.22));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.moveTo(a.x + nx * a.r, a.y + ny * a.r);
      ctx.lineTo(b.x + nx * b.r, b.y + ny * b.r);
      ctx.lineTo(b.x - nx * b.r, b.y - ny * b.r);
      ctx.lineTo(a.x - nx * a.r, a.y - ny * a.r);
      ctx.closePath();
      ctx.fill();
    }
    function draw(op) { return op.seg ? drawSeg(op) : drawJoint(op); }
    function add(op) { ops.push(op); draw(op); }
    function reset() {
      taken = {};
      used = 0;
      ops = [];
      pipe = null;
      colorAt = Math.floor(Math.random() * PP_COLORS.length);
      ctx.clearRect(0, 0, 800, 480);
    }
    function freeDirs(p) {
      var out = [];
      for (var i = 0; i < PP_DIRS.length; i++) {
        var q = [p[0] + PP_DIRS[i][0], p[1] + PP_DIRS[i][1], p[2] + PP_DIRS[i][2]];
        if (inside(q) && !taken[key(q)]) out.push(i);
      }
      return out;
    }
    function startPipe() {
      for (var tries = 0; tries < 60; tries++) {
        var p = [Math.floor(Math.random() * PP_NX), Math.floor(Math.random() * PP_NY), Math.floor(Math.random() * PP_NZ)];
        var dirs = !taken[key(p)] && freeDirs(p);
        if (!dirs || !dirs.length) continue;
        taken[key(p)] = true;
        used++;
        pipe = { p: p, d: dirs[Math.floor(Math.random() * dirs.length)], color: PP_COLORS[colorAt++ % PP_COLORS.length],
          left: PP_LEN_MIN + Math.floor(Math.random() * (PP_LEN_MAX - PP_LEN_MIN)) };
        add({ p: p, color: pipe.color, ball: false }); // rounded start
        return true;
      }
      return false;
    }
    function step() {
      if (!pipe && !startPipe()) { hold = PP_HOLD_S; return; }
      var dirs = freeDirs(pipe.p);
      // Boxed in, or long enough: round the end off; the next step starts a new pipe.
      if (!dirs.length || pipe.left-- <= 0) { add({ p: pipe.p, color: pipe.color, ball: false }); pipe = null; return; }
      var d = pipe.d;
      if (dirs.indexOf(d) < 0 || Math.random() < PP_TURN) {
        var turns = dirs.filter(function (i) { return i !== pipe.d; });
        d = turns.length ? turns[Math.floor(Math.random() * turns.length)] : d;
      }
      var q = [pipe.p[0] + PP_DIRS[d][0], pipe.p[1] + PP_DIRS[d][1], pipe.p[2] + PP_DIRS[d][2]];
      add({ seg: true, a: pipe.p, b: q, color: pipe.color });
      if (d !== pipe.d) {
        // The joint covers the corner where the pipe turned.
        add({ p: pipe.p, color: pipe.color, ball: o.joints === "ball" || (o.joints === "mixed" && Math.random() < PP_BALL) });
      }
      taken[key(q)] = true;
      used++;
      pipe.p = q;
      pipe.d = d;
      if (used >= total * PP_FILL) hold = PP_HOLD_S;
    }
    reset();
    return {
      frame: function (dt) {
        if (hold > 0) {
          hold -= dt;
          if (hold <= 0) reset();
          return;
        }
        acc += dt * rate;
        while (acc >= 1 && hold <= 0) { acc -= 1; step(); }
      },
      recolor: function () {
        ctx.clearRect(0, 0, 800, 480);
        for (var i = 0; i < ops.length; i++) draw(ops[i]);
      }
    };
  }

  // ── Windows 98 screensavers, drawn whole each frame on an opaque canvas ──
  // Dimming (no backlight) lays black over the finished frame, so their colours need no
  // per-colour scaling and recolor() has nothing to do.
  function svDim(ctx) {
    if (paletteF >= 1) return;
    ctx.fillStyle = "rgba(0,0,0," + (1 - paletteF).toFixed(3) + ")";
    ctx.fillRect(0, 0, 800, 480);
  }
  function svNoop() {}

  // Flying Through Space: stars stream out of the centre towards you, growing and brightening
  // as they come. Each star keeps its x/y and closes in on z; past the screen it starts again
  // far away. Stars are bucketed by brightness so a frame changes fill colour only a few times.
  var SF_COUNT = { light: 220, normal: 420, dense: 760 };
  var SF_Z_S = 0.42, SF_FOCAL = 360, SF_NEAR = 0.04, SF_LEVELS = 8;
  var SF_COLORS = ["#FFFFFF", "#9FC5FF", "#FFE08A", "#FF9F9F", "#B6FFB0", "#E0B0FF"];
  function starfieldSaver(el) {
    var o = cfg.idle.saver.starfield;
    var ctx = el.querySelector("[data-sv]").getContext("2d");
    var rate = SF_Z_S * (SPEEDS[o.speed] || 1);
    var stars = [];
    function place(s, far) {
      s.x = (Math.random() * 2 - 1) * 1.15; // most of them start on screen
      s.y = (Math.random() * 2 - 1) * 0.7;
      s.z = far ? 1 : SF_NEAR + Math.random() * (1 - SF_NEAR);
      s.c = Math.floor(Math.random() * SF_COLORS.length);
    }
    for (var i = 0; i < (SF_COUNT[o.density] || SF_COUNT.normal); i++) { var s = {}; place(s, false); stars.push(s); }
    // Fill colours per brightness level (and colour, when coloured).
    var fills = [];
    for (var c = 0; c < SF_COLORS.length; c++) {
      fills.push([]);
      for (var l = 0; l < SF_LEVELS; l++) fills[c].push(hexScale(o.color ? SF_COLORS[c] : "#FFFFFF", (l + 1) / SF_LEVELS));
    }
    var buckets = [];
    return {
      frame: function (dt) {
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, 800, 480);
        buckets.length = 0;
        for (var i = 0; i < stars.length; i++) {
          var s = stars[i];
          s.z -= rate * dt;
          var sx = 400 + (s.x / s.z) * SF_FOCAL, sy = 240 + (s.y / s.z) * SF_FOCAL;
          if (s.z <= SF_NEAR || sx < -4 || sx > 804 || sy < -4 || sy > 484) { place(s, true); continue; }
          var near = 1 - s.z; // 0 far .. 1 close
          var level = Math.min(SF_LEVELS - 1, Math.floor((0.3 + 0.7 * near) * SF_LEVELS)); // far ones still show
          var k = (o.color ? s.c : 0) * SF_LEVELS + level;
          (buckets[k] || (buckets[k] = [])).push(sx, sy, 1.2 + near * near * 3.6);
        }
        for (var b = 0; b < buckets.length; b++) {
          var list = buckets[b];
          if (!list) continue;
          ctx.fillStyle = fills[Math.floor(b / SF_LEVELS)][b % SF_LEVELS];
          for (var j = 0; j < list.length; j += 3) ctx.fillRect(list[j] - list[j + 2] / 2, list[j + 1] - list[j + 2] / 2, list[j + 2], list[j + 2]);
        }
        svDim(ctx);
      },
      recolor: svNoop
    };
  }

  // Mystify Your Mind: four-cornered shapes whose corners bounce off the edges, each leaving a
  // trail of earlier outlines that fade out, while its colour drifts round the wheel. A trail
  // copy is taken MY_COPIES_S times a second, so the trail's length on screen follows speed.
  var MY_PX_S = 150, MY_COPIES_S = 14, MY_HUE_S = 14;
  var MY_TRAIL = { short: 5, normal: 11, long: 20 };
  function mystifySaver(el) {
    var o = cfg.idle.saver.mystify;
    var ctx = el.querySelector("[data-sv]").getContext("2d");
    var speed = SPEEDS[o.speed] || 1, keep = MY_TRAIL[o.trail] || MY_TRAIL.normal;
    var shapes = [];
    for (var n = 0; n < o.shapes; n++) {
      var pts = [];
      for (var p = 0; p < 4; p++) {
        var ang = Math.random() * Math.PI * 2, v = MY_PX_S * (0.6 + Math.random() * 0.8);
        pts.push({ x: 40 + Math.random() * 720, y: 40 + Math.random() * 400, vx: Math.cos(ang) * v, vy: Math.sin(ang) * v });
      }
      shapes.push({ pts: pts, hue: n * (360 / o.shapes) + Math.random() * 40, trail: [] });
    }
    var acc = 0;
    function outline(poly) {
      ctx.beginPath();
      ctx.moveTo(poly[0], poly[1]);
      for (var i = 2; i < poly.length; i += 2) ctx.lineTo(poly[i], poly[i + 1]);
      ctx.closePath();
      ctx.stroke();
    }
    return {
      frame: function (dt) {
        dt *= speed;
        acc += dt * MY_COPIES_S;
        var copy = acc >= 1;
        if (copy) acc -= Math.floor(acc);
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, 800, 480);
        ctx.lineWidth = 2;
        for (var s = 0; s < shapes.length; s++) {
          var sh = shapes[s], poly = [];
          sh.hue = (sh.hue + MY_HUE_S * dt) % 360;
          for (var i = 0; i < sh.pts.length; i++) {
            var q = sh.pts[i];
            q.x += q.vx * dt;
            q.y += q.vy * dt;
            if (q.x < 0 || q.x > 800) { q.x = Math.max(0, Math.min(800, q.x)); q.vx = -q.vx; }
            if (q.y < 0 || q.y > 480) { q.y = Math.max(0, Math.min(480, q.y)); q.vy = -q.vy; }
            poly.push(q.x, q.y);
          }
          if (copy) {
            sh.trail.push(poly);
            if (sh.trail.length > keep) sh.trail.shift();
          }
          // Oldest first and faintest; the live outline last, at full colour.
          for (var t = 0; t < sh.trail.length; t++) {
            ctx.strokeStyle = "hsl(" + Math.round(sh.hue) + ",100%," + Math.round(12 + 38 * (t + 1) / (sh.trail.length + 1)) + "%)";
            outline(sh.trail[t]);
          }
          ctx.strokeStyle = "hsl(" + Math.round(sh.hue) + ",100%,60%)";
          outline(poly);
        }
        svDim(ctx);
      },
      recolor: svNoop
    };
  }

  // 3D Flower Box: a spinning cube, each side a grid of FB_N × FB_N tiles, that swells into a
  // round, spiky "flower" and back again. Every grid point lies on the cube; morphing moves it
  // towards the sphere through it, and points in the middle of each tile also push out as
  // spikes. Tiles are flat-shaded by their facing to a light and drawn back to front.
  var FB_N = 6, FB_SPIN_S = 0.55, FB_MORPH_S = 0.16, FB_R = 1.3, FB_SPIKE = 0.85;
  var FB_D = 5.2, FB_SCALE = 570; // camera distance; scale: the cube ~220 px, the full flower ~470 px
  var FB_LIGHT = (function () { var v = [-0.45, -0.55, -0.7], l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); return [v[0] / l, v[1] / l, v[2] / l]; })();
  var FB_PAIRS = [["#E53935", "#FFD54F"], ["#1E88E5", "#E0F2F1"], ["#43A047", "#F48FB1"],
    ["#8E24AA", "#80DEEA"], ["#FB8C00", "#3949AB"], ["#00ACC1", "#FFF176"]];
  function rgbOf(hex) { var n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function flowerboxSaver(el) {
    var o = cfg.idle.saver.flowerbox;
    var ctx = el.querySelector("[data-sv]").getContext("2d");
    var speed = SPEEDS[o.speed] || 1;
    // Each side: an origin corner and two edge vectors spanning it.
    var SIDES = [
      [[-1, -1, 1], [2, 0, 0], [0, 2, 0]], [[1, -1, -1], [-2, 0, 0], [0, 2, 0]],
      [[1, -1, 1], [0, 0, -2], [0, 2, 0]], [[-1, -1, -1], [0, 0, 2], [0, 2, 0]],
      [[-1, -1, -1], [2, 0, 0], [0, 0, 2]], [[-1, 1, 1], [2, 0, 0], [0, 0, -2]]
    ];
    var pts = [], tiles = [];
    for (var f = 0; f < SIDES.length; f++) {
      var s = SIDES[f], base = pts.length;
      for (var j = 0; j <= FB_N; j++) {
        for (var i = 0; i <= FB_N; i++) {
          var c = [s[0][0] + s[1][0] * i / FB_N + s[2][0] * j / FB_N, s[0][1] + s[1][1] * i / FB_N + s[2][1] * j / FB_N,
            s[0][2] + s[1][2] * i / FB_N + s[2][2] * j / FB_N];
          var len = Math.sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]);
          // Spikes on the odd grid points: never on an edge, so neighbouring sides agree.
          pts.push({ c: c, d: [c[0] / len, c[1] / len, c[2] / len], spike: i % 2 && j % 2 ? 1 : 0 });
        }
      }
      for (j = 0; j < FB_N; j++) {
        for (i = 0; i < FB_N; i++) {
          var a = base + j * (FB_N + 1) + i;
          var pair = FB_PAIRS[f];
          var hex = o.colors === "faces" ? pair[0] : pair[(i + j) % 2];
          tiles.push({ v: [a, a + 1, a + FB_N + 2, a + FB_N + 1], rgb: rgbOf(hex) });
        }
      }
    }
    var ax = 0.4, ay = 0.2, az = 0, phase = 0;
    var cam = new Array(pts.length), scr = new Array(pts.length);
    return {
      frame: function (dt) {
        dt *= speed;
        ax += FB_SPIN_S * dt * 0.7;
        ay += FB_SPIN_S * dt;
        az += FB_SPIN_S * dt * 0.35;
        phase += FB_MORPH_S * dt * Math.PI * 2;
        var m = (1 - Math.cos(phase)) / 2; // 0 cube .. 1 flower
        var cx = Math.cos(ax), sx = Math.sin(ax), cy = Math.cos(ay), sy = Math.sin(ay), cz = Math.cos(az), sz = Math.sin(az);
        for (var k = 0; k < pts.length; k++) {
          var p = pts[k], r = FB_R + FB_SPIKE * p.spike;
          var x = p.c[0] * (1 - m) + p.d[0] * r * m, y = p.c[1] * (1 - m) + p.d[1] * r * m, z = p.c[2] * (1 - m) + p.d[2] * r * m;
          var t1 = y * cx - z * sx; z = y * sx + z * cx; y = t1; // about x
          t1 = x * cy + z * sy; z = -x * sy + z * cy; x = t1; // about y
          t1 = x * cz - y * sz; y = x * sz + y * cz; x = t1; // about z
          cam[k] = [x, y, z];
          var q = FB_SCALE / (FB_D + z);
          scr[k] = [400 + x * q, 240 + y * q];
        }
        var order = [];
        for (var t = 0; t < tiles.length; t++) {
          var v = tiles[t].v, A = cam[v[0]], B = cam[v[1]], C = cam[v[2]], D = cam[v[3]];
          // Normal from the diagonals, turned outwards (away from the centre).
          var e1 = [C[0] - A[0], C[1] - A[1], C[2] - A[2]], e2 = [D[0] - B[0], D[1] - B[1], D[2] - B[2]];
          var nx = e1[1] * e2[2] - e1[2] * e2[1], ny = e1[2] * e2[0] - e1[0] * e2[2], nz = e1[0] * e2[1] - e1[1] * e2[0];
          var mx = A[0] + B[0] + C[0] + D[0], my = A[1] + B[1] + C[1] + D[1], mz = A[2] + B[2] + C[2] + D[2];
          if (nx * mx + ny * my + nz * mz < 0) { nx = -nx; ny = -ny; nz = -nz; }
          var nl = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
          var lit = Math.max(0, (nx * FB_LIGHT[0] + ny * FB_LIGHT[1] + nz * FB_LIGHT[2]) / nl);
          order.push({ t: t, z: mz, lit: 0.22 + 0.78 * lit });
        }
        order.sort(function (a, b) { return b.z - a.z; }); // far first
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, 800, 480);
        for (var w = 0; w < order.length; w++) {
          var tile = tiles[order[w].t], f2 = order[w].lit, rgb = tile.rgb, vv = tile.v;
          ctx.fillStyle = "rgb(" + Math.round(rgb[0] * f2) + "," + Math.round(rgb[1] * f2) + "," + Math.round(rgb[2] * f2) + ")";
          ctx.beginPath();
          ctx.moveTo(scr[vv[0]][0], scr[vv[0]][1]);
          ctx.lineTo(scr[vv[1]][0], scr[vv[1]][1]);
          ctx.lineTo(scr[vv[2]][0], scr[vv[2]][1]);
          ctx.lineTo(scr[vv[3]][0], scr[vv[3]][1]);
          ctx.closePath();
          ctx.fill();
          ctx.strokeStyle = ctx.fillStyle; // closes hairline gaps between neighbouring tiles
          ctx.lineWidth = 0.75;
          ctx.stroke();
        }
        svDim(ctx);
      },
      recolor: svNoop
    };
  }

  // 3D Maze: walking a fresh maze, Wolfenstein-style. A raycaster draws it at MZ_RW × MZ_RH
  // (chunky pixels, like the original) into an offscreen canvas that is scaled up 4×, which
  // keeps it cheap with no GPU. Textures are made here, so there are no image files. The
  // walker keeps its left hand on the wall, which in a maze with no loops always reaches the
  // exit (the smiley); then a new maze starts. A rat wanders the corridors.
  var MZ_W = 8, MZ_H = 8, MZ_RW = 200, MZ_RH = 120, MZ_TEX = 64;
  var MZ_FOV = 0.66, MZ_MOVE_S = 1.6, MZ_TURN_S = 2.2, MZ_FOG = 9, MZ_RAT_S = 1.2, MZ_HOLD_S = 1.2;
  var MZ_DX = [1, 0, -1, 0], MZ_DY = [0, 1, 0, -1]; // east, south, west, north (y grows down)
  function mzRng(seed) { var s = seed >>> 0; return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }
  function mzPix(r, g, b, a) {
    return ((a === undefined ? 255 : a) << 24 | Math.max(0, Math.min(255, b)) << 16 | Math.max(0, Math.min(255, g)) << 8 | Math.max(0, Math.min(255, r))) >>> 0;
  }
  function mzTexture(kind) {
    var T = MZ_TEX, out = new Uint32Array(T * T), rnd = mzRng(kind.length * 977 + 13);
    for (var y = 0; y < T; y++) {
      for (var x = 0; x < T; x++) {
        var n = (rnd() - 0.5) * 30, c;
        if (kind === "brick") {
          var row = Math.floor(y / 16), bx = (x + (row % 2) * 16) % 32;
          c = (y % 16 < 2 || bx < 2) ? [168 + n / 3, 162 + n / 3, 150 + n / 3] : [148 + n, 52 + n / 2, 38 + n / 2];
        } else if (kind === "stone") {
          var srow = Math.floor(y / 21), sx2 = (x + srow * 11) % 32;
          c = (y % 21 < 2 || sx2 < 2) ? [58, 58, 64] : [118 + n, 118 + n, 124 + n];
        } else if (kind === "wood") {
          var plank = Math.floor(x / 16), grain = Math.sin(y * 0.35 + plank * 7 + Math.sin(x * 0.9) * 1.5) * 14;
          c = x % 16 < 1 ? [70, 44, 24] : [138 + grain + n / 2, 92 + grain / 2 + n / 3, 52 + n / 4];
        } else if (kind === "floor") {
          var on = (Math.floor(x / 32) + Math.floor(y / 32)) % 2;
          c = on ? [92 + n / 2, 92 + n / 2, 98 + n / 2] : [70 + n / 2, 70 + n / 2, 76 + n / 2];
        } else { // ceiling
          c = (x % 32 < 1 || y % 32 < 1) ? [28, 32, 52] : [44 + n / 3, 50 + n / 3, 82 + n / 3];
        }
        out[y * T + x] = mzPix(Math.round(c[0]), Math.round(c[1]), Math.round(c[2]));
      }
    }
    return out;
  }
  // Sprites, 32×32 with see-through pixels: the rat (side on) and the exit's smiley.
  function mzSprite(kind) {
    var S = 32, out = new Uint32Array(S * S);
    function inEllipse(x, y, cx, cy, rx, ry) { var a = (x - cx) / rx, b = (y - cy) / ry; return a * a + b * b <= 1; }
    for (var y = 0; y < S; y++) {
      for (var x = 0; x < S; x++) {
        var px = 0;
        if (kind === "rat") {
          if (inEllipse(x, y, 15, 24, 10, 6)) px = mzPix(112, 108, 112);
          if (inEllipse(x, y, 25, 22, 5, 4)) px = mzPix(124, 120, 124);
          if (inEllipse(x, y, 23, 17, 2, 2.5)) px = mzPix(214, 150, 160);
          if (x === 27 && y === 21) px = mzPix(10, 10, 10);
          if (x === 30 && y === 23) px = mzPix(214, 150, 160);
          if (x <= 6 && Math.abs(y - (26 + (6 - x) * 0.5)) < 0.8) px = mzPix(200, 140, 150); // tail
          if ((x === 10 || x === 20) && y >= 29 && y <= 31) px = mzPix(90, 86, 90); // legs
        } else {
          var d = Math.sqrt((x - 15.5) * (x - 15.5) + (y - 15.5) * (y - 15.5));
          if (d <= 14) px = d > 13 ? mzPix(150, 110, 0) : mzPix(255, 214, 40);
          if (inEllipse(x, y, 11, 12, 1.6, 2.6) || inEllipse(x, y, 20, 12, 1.6, 2.6)) px = mzPix(20, 20, 20);
          if (d > 7.5 && d < 9.5 && y > 17) px = mzPix(20, 20, 20); // smile
        }
        out[y * S + x] = px;
      }
    }
    return out;
  }
  function mzShade(c, f) {
    return ((c & 0xFF000000) | ((((c >> 16) & 255) * f) << 16) | ((((c >> 8) & 255) * f) << 8) | ((c & 255) * f)) >>> 0;
  }
  function mazeSaver(el) {
    var o = cfg.idle.saver.maze;
    var ctx = el.querySelector("[data-sv]").getContext("2d");
    var speed = SPEEDS[o.speed] || 1;
    var off = document.createElement("canvas");
    off.width = MZ_RW;
    off.height = MZ_RH;
    var octx = off.getContext("2d"), img = octx.createImageData(MZ_RW, MZ_RH);
    var buf = new Uint32Array(img.data.buffer), zbuf = new Float32Array(MZ_RW);
    var wallTex = mzTexture(o.walls || "brick"), floorTex = mzTexture("floor"), ceilTex = mzTexture("ceiling");
    var ratTex = mzSprite("rat"), smileTex = mzSprite("smile");
    var GW = MZ_W * 2 + 1, GH = MZ_H * 2 + 1;
    var map, walker, rat, exit, hold = 0, bob = 0;

    function open(x, y) { return x >= 0 && y >= 0 && x < GW && y < GH && map[y * GW + x] === 0; }
    function build() {
      map = new Uint8Array(GW * GH);
      for (var i = 0; i < map.length; i++) map[i] = 1;
      // Depth-first carving from the first cell: a maze with exactly one way between any two cells.
      var stack = [[0, 0]], seen = {};
      seen["0,0"] = true;
      map[1 * GW + 1] = 0;
      while (stack.length) {
        var c = stack[stack.length - 1], next = [];
        for (var d = 0; d < 4; d++) {
          var nx = c[0] + MZ_DX[d], ny = c[1] + MZ_DY[d];
          if (nx >= 0 && ny >= 0 && nx < MZ_W && ny < MZ_H && !seen[nx + "," + ny]) next.push(d);
        }
        if (!next.length) { stack.pop(); continue; }
        var dd = next[Math.floor(Math.random() * next.length)], n2 = [c[0] + MZ_DX[dd], c[1] + MZ_DY[dd]];
        seen[n2[0] + "," + n2[1]] = true;
        map[(2 * c[1] + 1 + MZ_DY[dd]) * GW + 2 * c[0] + 1 + MZ_DX[dd]] = 0; // the wall between
        map[(2 * n2[1] + 1) * GW + 2 * n2[0] + 1] = 0;
        stack.push(n2);
      }
      // The exit: the cell farthest from the start.
      var dist = {}, queue = [[1, 1]], far = [1, 1];
      dist["1,1"] = 0;
      while (queue.length) {
        var q = queue.shift();
        for (var k = 0; k < 4; k++) {
          var x2 = q[0] + MZ_DX[k], y2 = q[1] + MZ_DY[k], key = x2 + "," + y2;
          if (!open(x2, y2) || key in dist) continue;
          dist[key] = dist[q[0] + "," + q[1]] + 1;
          if (x2 % 2 && y2 % 2 && dist[key] > dist[far[0] + "," + far[1]]) far = [x2, y2];
          queue.push([x2, y2]);
        }
      }
      exit = far;
      var dir = open(2, 1) ? 0 : 1;
      walker = { x: 1, y: 1, dir: dir, ang: dir * Math.PI / 2, act: null, mustMove: false };
      rat = { x: exit[0], y: exit[1], from: null, to: null, t: 0 };
      hold = 0;
    }
    function plan() {
      var w = walker, l = (w.dir + 3) % 4, r = (w.dir + 1) % 4;
      if (w.x === exit[0] && w.y === exit[1]) { hold = MZ_HOLD_S; return; }
      if (w.mustMove || (!open(w.x + MZ_DX[l], w.y + MZ_DY[l]) && open(w.x + MZ_DX[w.dir], w.y + MZ_DY[w.dir]))) {
        w.mustMove = false;
        w.act = { kind: "move", t: 0, fx: w.x, fy: w.y, tx: w.x + MZ_DX[w.dir], ty: w.y + MZ_DY[w.dir] };
      } else {
        var to = open(w.x + MZ_DX[l], w.y + MZ_DY[l]) ? l : open(w.x + MZ_DX[r], w.y + MZ_DY[r]) ? r : r;
        w.mustMove = to === l || open(w.x + MZ_DX[to], w.y + MZ_DY[to]);
        w.act = { kind: "turn", t: 0, from: w.ang, to: w.ang + (to === l ? -1 : 1) * Math.PI / 2, dir: to };
      }
    }
    function advance(dt) {
      if (hold > 0) { hold -= dt; if (hold <= 0) build(); return; }
      var w = walker;
      if (!w.act) plan();
      if (!w.act) return;
      var a = w.act;
      a.t = Math.min(1, a.t + dt * (a.kind === "move" ? MZ_MOVE_S : MZ_TURN_S));
      var e = a.t * a.t * (3 - 2 * a.t); // ease in and out
      if (a.kind === "turn") w.ang = a.from + (a.to - a.from) * e;
      if (a.t >= 1) {
        if (a.kind === "move") { w.x = a.tx; w.y = a.ty; } else { w.dir = a.dir; w.ang = a.to; }
        w.act = null;
      }
    }
    function moveRat(dt) {
      if (!rat.to) {
        var opts = [];
        for (var d = 0; d < 4; d++) {
          var nx = rat.x + MZ_DX[d], ny = rat.y + MZ_DY[d];
          if (open(nx, ny) && !(rat.from && rat.from[0] === nx && rat.from[1] === ny)) opts.push([nx, ny]);
        }
        rat.to = opts.length ? opts[Math.floor(Math.random() * opts.length)] : rat.from;
        rat.t = 0;
      }
      rat.t += dt * MZ_RAT_S;
      if (rat.t >= 1) { rat.from = [rat.x, rat.y]; rat.x = rat.to[0]; rat.y = rat.to[1]; rat.to = null; rat.t = 0; }
    }
    function fog(dist) { return Math.max(0.12, 1 - dist / MZ_FOG); }
    function render() {
      var w = walker, a = w.act, px = w.x + 0.5, py = w.y + 0.5;
      if (a && a.kind === "move") {
        var e = a.t * a.t * (3 - 2 * a.t);
        px = a.fx + (a.tx - a.fx) * e + 0.5;
        py = a.fy + (a.ty - a.fy) * e + 0.5;
      }
      var dirX = Math.cos(w.ang), dirY = Math.sin(w.ang), plX = -dirY * MZ_FOV, plY = dirX * MZ_FOV;
      var W = MZ_RW, H = MZ_RH, T = MZ_TEX, half = H / 2, x, y;
      // Floor and ceiling, a row at a time.
      for (y = Math.floor(half) + 1; y < H; y++) {
        var rowDist = half / (y - half), f = fog(rowDist);
        var fx = px + rowDist * (dirX - plX), fy = py + rowDist * (dirY - plY);
        var stX = rowDist * 2 * plX / W, stY = rowDist * 2 * plY / W, top = (H - y - 1) * W, bot = y * W;
        for (x = 0; x < W; x++) {
          var tx = (fx * T) & (T - 1), ty = (fy * T) & (T - 1);
          buf[bot + x] = mzShade(floorTex[ty * T + tx], f);
          buf[top + x] = mzShade(ceilTex[ty * T + tx], f);
          fx += stX;
          fy += stY;
        }
      }
      // Walls: one ray per column, stepped cell by cell until it meets a wall.
      for (x = 0; x < W; x++) {
        var camX = 2 * x / W - 1, rx = dirX + plX * camX, ry = dirY + plY * camX;
        var mx = Math.floor(px), my = Math.floor(py);
        var ddx = rx === 0 ? 1e30 : Math.abs(1 / rx), ddy = ry === 0 ? 1e30 : Math.abs(1 / ry);
        var sX = rx < 0 ? -1 : 1, sY = ry < 0 ? -1 : 1;
        var sdx = (rx < 0 ? px - mx : mx + 1 - px) * ddx, sdy = (ry < 0 ? py - my : my + 1 - py) * ddy, side = 0;
        for (var guard = 0; guard < 64; guard++) {
          if (sdx < sdy) { sdx += ddx; mx += sX; side = 0; } else { sdy += ddy; my += sY; side = 1; }
          if (!open(mx, my)) break;
        }
        var dist = side === 0 ? sdx - ddx : sdy - ddy;
        if (dist < 0.05) dist = 0.05;
        zbuf[x] = dist;
        var lh = H / dist, start = Math.max(0, Math.floor(half - lh / 2)), end = Math.min(H - 1, Math.floor(half + lh / 2));
        var hit = side === 0 ? py + dist * ry : px + dist * rx;
        var texX = Math.floor((hit - Math.floor(hit)) * T);
        if ((side === 0 && rx > 0) || (side === 1 && ry < 0)) texX = T - texX - 1;
        var shade = fog(dist) * (side ? 0.72 : 1), step = T / lh, pos = (start - half + lh / 2) * step;
        for (y = start; y <= end; y++) {
          buf[y * W + x] = mzShade(wallTex[(Math.floor(pos) & (T - 1)) * T + texX], shade);
          pos += step;
        }
      }
      // Sprites: the smiley at the exit (floating, bobbing) and the rat (on the floor).
      var sprites = [{ x: exit[0] + 0.5, y: exit[1] + 0.5, tex: smileTex, size: 0.5, floor: false }];
      if (o.rat) {
        var rt = rat.to ? rat.t : 0, rxp = rat.to ? rat.x + (rat.to[0] - rat.x) * rt : rat.x, ryp = rat.to ? rat.y + (rat.to[1] - rat.y) * rt : rat.y;
        sprites.push({ x: rxp + 0.5, y: ryp + 0.5, tex: ratTex, size: 0.32, floor: true });
      }
      var inv = 1 / (plX * dirY - dirX * plY);
      sprites.sort(function (s1, s2) { return ((s2.x - px) * (s2.x - px) + (s2.y - py) * (s2.y - py)) - ((s1.x - px) * (s1.x - px) + (s1.y - py) * (s1.y - py)); });
      for (var s = 0; s < sprites.length; s++) {
        var sp = sprites[s], ox = sp.x - px, oy = sp.y - py;
        var tX = inv * (dirY * ox - dirX * oy), tY = inv * (-plY * ox + plX * oy);
        if (tY <= 0.15) continue;
        var cx = Math.floor(W / 2 * (1 + tX / tY)), size = Math.abs(H / tY) * sp.size;
        var bottom = sp.floor ? half + H / (2 * tY) : half + Math.sin(bob) * 4 + size / 2;
        var y0 = Math.floor(bottom - size), x0 = Math.floor(cx - size / 2), sf = fog(tY);
        for (var sx = Math.max(0, x0); sx < Math.min(W, x0 + size); sx++) {
          if (tY >= zbuf[sx]) continue;
          var u = Math.floor((sx - x0) * 32 / size);
          for (var sy = Math.max(0, y0); sy < Math.min(H, y0 + size); sy++) {
            var c = sp.tex[Math.floor((sy - y0) * 32 / size) * 32 + u];
            if (c >>> 24) buf[sy * W + sx] = mzShade(c, sf);
          }
        }
      }
      octx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, 0, 0, 800, 480);
      svDim(ctx);
    }
    build();
    return {
      frame: function (dt) {
        dt *= speed;
        bob += dt * 3;
        advance(dt);
        if (o.rat) moveRat(dt);
        render();
      },
      recolor: svNoop
    };
  }

  var SAVERS = {
    logo: logoSaver, matrix: matrixSaver, pipes: pipesSaver,
    starfield: starfieldSaver, mystify: mystifySaver, flowerbox: flowerboxSaver, maze: mazeSaver
  };
  function startAnim() {
    stopAnim();
    var el = saver.querySelector("[data-saver]");
    var make = el && SAVERS[el.getAttribute("data-saver")];
    if (!make) return;
    anim = make(el);
    runAnim();
  }
  function stopAnim() {
    anim = null;
    clearTimeout(animTimer);
    animTimer = null;
  }
  // Runs while a style is on screen, the screen is lit and the page is visible.
  function runAnim() {
    if (!anim || animTimer || !animFps || document.hidden) return;
    animLast = Date.now();
    var step = function () {
      animTimer = null;
      if (!anim || !animFps || document.hidden) return;
      var now = Date.now();
      anim.frame(Math.min(0.2, (now - animLast) / 1000));
      animLast = now;
      animTimer = setTimeout(step, 1000 / animFps);
    };
    animTimer = setTimeout(step, 1000 / animFps);
  }
  document.addEventListener("visibilitychange", runAnim);

  function showSaver() {
    if (mode === "saver") return;
    mode = "saver";
    // Never leave a PIN pad or restart prompt up behind the screensaver.
    if (screenKind() === "restart" || screenKind() === "restarting") goHome();
    return fetch("v/saver", { credentials: "same-origin" }).then(function (r) {
      if (r.status === 403) { location.reload(); return null; }
      return r.ok ? r.text() : null;
    }).then(function (html) {
      if (html === null || mode !== "saver") return;
      saver.innerHTML = html;
      saver.hidden = false;
      view.hidden = true;
      paintTime(true);
      npLook(null);
      startAnim();
      applyLevel(true);
    }).catch(function () {});
  }
  function sleep() {
    asleep = true;
    if (mode === "saver") return applyLevel(true);
    var shown = showSaver();
    return shown && shown.then ? shown.then(function () { applyLevel(true); }) : applyLevel(true);
  }
  function refreshSaver() {
    // The animations show no feed data, but the music artwork can take their place.
    if (cfg.idle.saver.style !== "clock" && !cfg.music.saver_artwork) return;
    fetch("v/saver", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : null; }).then(function (html) {
      if (html === null || mode !== "saver") return;
      var wasMusic = !!saver.querySelector("[data-saver-music]");
      if (!wasMusic && cfg.idle.saver.style !== "clock" && html.indexOf("data-saver-music") < 0) return; // animation stays
      saver.innerHTML = html;
      paintTime(true);
      paintBars();
      npLook(null);
      startAnim();
      applyLevel(true);
    }).catch(function () {});
  }
  function wake() {
    asleep = false;
    mode = "active";
    stopAnim();
    saver.hidden = true;
    view.hidden = false;
    paintTime(true);
    showStrip();
    applyLevel(true);
  }

  function onInput(id) {
    lastInput = Date.now();
    if (mode !== "active") { wake(); return; }
    stripOnInput();
    npTouched();
    // Now Playing with its controls set to Never: the dial press still plays and pauses.
    if (id === "dial_press" && npShown() && !view.querySelector(".np2.ctl-shown") && allowed("music:play_pause")) return musicCmd("play_pause");
    if (dialIsVolume(id)) return volStep(id === "dial_cw" ? 1 : -1);
    perform(cfg.controls[id]);
  }

  // ── Hardware: keys (with long press) and the dial ──────────────────────
  var held = {};
  document.addEventListener("keydown", function (e) {
    var id = KEYS[e.key];
    if (!id) return;
    e.preventDefault();
    if (e.repeat) return;
    if (id === "dial_cw" || id === "dial_ccw") { onInput(id); return; }
    var longId = id + "_long";
    if (mode === "active" && cfg.controls[longId] && allowed(cfg.controls[longId])) {
      held[id] = { fired: false, timer: setTimeout(function () { held[id].fired = true; onInput(longId); }, LONG_MS) };
    } else {
      onInput(id);
    }
  });
  document.addEventListener("keyup", function (e) {
    var id = KEYS[e.key];
    var h = id && held[id];
    if (!h) return;
    clearTimeout(h.timer);
    if (!h.fired) onInput(id);
    delete held[id];
  });

  var wheelAcc = 0, wheelAt = 0;
  window.addEventListener("wheel", function (e) {
    e.preventDefault();
    var d = Math.abs(e.deltaX) >= Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    var now = Date.now();
    if (now - wheelAt > 150) wheelAcc = 0;
    wheelAt = now;
    wheelAcc += d;
    if (Math.abs(wheelAcc) >= 30) { onInput(wheelAcc > 0 ? "dial_cw" : "dial_ccw"); wheelAcc = 0; }
  }, { passive: false });

  // ── Touch: tap selects what's under the finger; swipes map to actions ──
  var touch = null;
  document.addEventListener("pointerdown", function (e) {
    touch = { x: e.clientX, y: e.clientY, t: Date.now(), target: e.target };
    touch.seek = mode === "active" && e.target.closest ? e.target.closest("[data-np-seek]") : null;
    if (touch.seek) {
      var np = touch.seek.closest(".np2");
      if (np) np.classList.add("is-seeking");
      seekPreview(touch.seek, e.clientX);
    }
    if (mode === "active") stripOnInput();
  });
  document.addEventListener("pointermove", function (e) {
    if (touch && touch.seek) seekPreview(touch.seek, e.clientX);
  });
  // The browser took the gesture over (native scroll in a list): not a tap or a swipe.
  document.addEventListener("pointercancel", function () {
    touch = null;
    lastInput = Date.now();
  });
  document.addEventListener("pointerup", function (e) {
    if (!touch) return;
    var dx = e.clientX - touch.x, dy = e.clientY - touch.y, dt = Date.now() - touch.t, start = touch.target;
    touch = null;
    if (mode !== "active") { lastInput = Date.now(); wake(); return; }
    if (seekRelease(start, e)) return;
    var ax = Math.abs(dx), ay = Math.abs(dy);
    if (dt < 500 && Math.max(ax, ay) >= 60) {
      if (ax > ay) return onInput(dx < 0 ? "swipe_left" : "swipe_right");
      // Vertical swipes inside a scrolling list/article scroll it instead.
      var scroller = start && start.closest ? start.closest(".rows, .doc") : null;
      if (scroller && scroller.scrollHeight > scroller.clientHeight) { lastInput = Date.now(); return; }
      return onInput(dy < 0 ? "swipe_up" : "swipe_down");
    }
    if (ax > 12 || ay > 12) { lastInput = Date.now(); return; } // a scroll, not a tap
    lastInput = Date.now();
    npTouched();
    var sheet = view.querySelector("[data-np-sheet]");
    if (sheet && !sheet.hidden && start && start.closest && !start.closest("[data-np-sheet], [data-np-more]")) npSheet(false);
    var dot = start && start.closest ? start.closest("[data-page-go]") : null;
    if (dot) return perform("page:" + dot.getAttribute("data-page-go"));
    var el = start && start.closest ? start.closest("[data-focus]") : null;
    if (!el) return;
    var items = focusables();
    for (var i = 0; i < items.length; i++) if (items[i] === el) setFocus(i);
    activate(el);
  });

  // A press that started on the progress line: seek to where the finger came up.
  function seekRelease(start, e) {
    var seek = start && start.closest ? start.closest("[data-np-seek]") : null;
    if (!seek) return false;
    var np = seek.closest(".np2");
    if (np) np.classList.remove("is-seeking");
    lastInput = Date.now();
    seekTo(seek, e.clientX);
    return true;
  }

  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { toastEl.hidden = true; }, 3500);
  }

  // The device has no devtools: send script errors to the Pi's journal (stonepi-carthing).
  var reported = 0;
  function report(text) {
    if (reported >= 20) return;
    reported += 1;
    try {
      fetch("clientlog", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ msg: String(text).slice(0, 400), ua: navigator.userAgent })
      }).catch(function () {});
    } catch (e) { /* nothing more to do */ }
  }
  // A failed request rejects with TypeError "Failed to fetch"; anything else is a script error
  // in the rendering, which must not be reported as the Pi being down.
  function fail(what, err) {
    var msg = String((err && err.message) || err);
    var network = err instanceof TypeError && /fetch|network/i.test(msg);
    if (network) setOnline(false);
    toast(network ? "StonePi isn’t answering." : "Panel error: " + msg);
    report(what + (network ? " (network): " : ": ") + msg + (err && err.stack ? " | " + err.stack : ""));
  }
  window.addEventListener("error", function (e) {
    report("onerror: " + e.message + " @ " + e.filename + ":" + e.lineno + ":" + e.colno);
  });
  window.addEventListener("unhandledrejection", function (e) {
    report("unhandled: " + String(e.reason && e.reason.message || e.reason));
  });

  // ── Web page snapshots (F14): a fresh one every data-web-every s, only while it shows ──
  // The Pi captures the page (Notify) and caches it, so asking costs the device one image.
  // The new image loads off screen first, so the old one stays up until it's ready.
  function refreshWeb() {
    if (document.hidden) return;
    var imgs = (mode === "saver" ? saver : view).querySelectorAll("img[data-web-every]");
    var now = Date.now();
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      var at = Number(img.getAttribute("data-web-at") || 0);
      if (!at) { img.setAttribute("data-web-at", String(now)); continue; }
      if (img.getAttribute("data-web-loading") || now - at < Number(img.getAttribute("data-web-every")) * 1000) continue;
      loadWeb(img);
    }
  }
  // The fresh image loads into a copy that replaces the old one, so each refresh is one
  // download (the snapshot is no-store, so setting the old img's src would fetch it again).
  function loadWeb(img) {
    var next = img.getAttribute("src").split("&r=")[0] + "&r=" + Date.now();
    var fresh = document.createElement("img");
    for (var a = 0; a < img.attributes.length; a++) {
      var attr = img.attributes[a];
      if (attr.name !== "src" && attr.name !== "data-web-loading") fresh.setAttribute(attr.name, attr.value);
    }
    img.setAttribute("data-web-loading", "1");
    fresh.onload = function () {
      fresh.onload = fresh.onerror = null;
      fresh.setAttribute("data-web-at", String(Date.now()));
      if (img.parentNode) {
        img.parentNode.replaceChild(fresh, img);
        webShown(fresh, true);
      }
    };
    fresh.onerror = function () { // keep the last image
      fresh.onload = fresh.onerror = null;
      img.removeAttribute("data-web-loading");
      img.setAttribute("data-web-at", String(Date.now()));
    };
    fresh.src = next;
  }
  function webShown(img, ok) {
    var msg = img.parentNode && img.parentNode.querySelector("[data-web-msg]");
    if (msg) msg.hidden = ok;
    img.style.visibility = ok ? "" : "hidden";
  }
  document.addEventListener("load", function (e) {
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-web-every")) webShown(e.target, true);
  }, true);
  document.addEventListener("error", function (e) {
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-web-every")) webShown(e.target, false);
  }, true);

  // ── Check in with the Pi every 15 s (every 3 s while the link is down) ──
  // The panel reaches the Pi only through the USB link (adb reverse). When that drops the
  // screen keeps its last page, so a banner says so, and the view reloads once it's back.
  var online = true;
  var TICK_MS = 15000, RETRY_MS = 3000;
  function setOnline(up) {
    if (up === online) return;
    online = up;
    offlineEl.hidden = up;
    if (up) {
      toastEl.hidden = true;
      if (mode === "saver") refreshSaver();
      else load(current, false, focusIndex, true);
    }
  }
  var frev = "";
  function tick() {
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
    fetch("tick", { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined }).then(function (r) {
      clearTimeout(timer);
      if (r.status === 403) { location.reload(); return null; }
      setOnline(true);
      return r.ok ? r.json() : null;
    }).then(function (t) {
      if (!t) return;
      clockOffset = t.now_ms - Date.now();
      tzMin = t.tz_min;
      if (t.rev && t.rev !== cfg.rev) { location.reload(); return; }
      if (backlight !== !!t.backlight) { backlight = !!t.backlight; applyLevel(true); }
      if (frev && t.frev !== frev) {
        if (mode === "saver") refreshSaver();
        else if (screenKind() === "home" || screenKind() === "app" || screenKind() === "weather") load(current, false, focusIndex, true);
      }
      frev = t.frev;
    }).catch(function () {
      clearTimeout(timer);
      setOnline(false);
    });
  }
  function scheduleTick() {
    setTimeout(function () { tick(); scheduleTick(); }, online ? TICK_MS : RETRY_MS);
  }

  // Page rotation: only while nobody is using the panel. Any input pauses it; after
  // resume_after_s without one, an open mini-app closes back to its page and rotation goes on.
  // The screensaver (and quiet hours) win: rotation never runs behind it.
  var rotatePreview = false;
  function rotate(idle) {
    var every = cfg.rotation.every_s;
    if (!every || pageCount < 2 || mode !== "active" || loading) return;
    if (cfg.preview && !rotatePreview) return;
    if (idle < cfg.rotation.resume_after_s) return;
    if (screenKind() !== "home" || stack.length) { goHome(); return; }
    if (Date.now() - pageShownAt >= every * 1000) goPage(pageIndex + 1);
  }

  // One 1 s timer: clock text + idle state. Nothing repaints unless something changed.
  var QUIET_SAVER_S = 60;
  setInterval(function () {
    paintTime(false);
    paintBars();
    refreshWeb();
    var idle = (Date.now() - lastInput) / 1000;
    var saverAfter = cfg.idle.screensaver_after_s;
    // 0 = no screensaver by day; quiet hours still bring it after a minute untouched.
    if (mode === "active" && !cfg.preview && ((saverAfter > 0 && idle >= saverAfter) || (inQuiet() && idle >= QUIET_SAVER_S))) showSaver();
    rotate(idle);
    if (mode === "saver") {
      applyLevel(false);
      // Clear Chromium memory creep once a day, while nobody is looking.
      if (inQuiet() && Date.now() - loadedAt > 20 * 3600 * 1000) location.reload();
    }
  }, 1000);
  scheduleTick();

  // Notify's editor drives the preview (buttons on the drawing, "show screensaver", ...).
  if (cfg.preview) {
    window.addEventListener("message", function (e) {
      if (e.source !== window.parent || !e.data || e.data.source !== "stonepi-carthing") return;
      if (e.data.type === "input") onInput(String(e.data.id));
      else if (e.data.type === "saver") { lastInput = Date.now(); showSaver(); }
      else if (e.data.type === "wake") { lastInput = Date.now(); wake(); }
      else if (e.data.type === "open") { lastInput = Date.now(); if (mode !== "active") wake(); perform(String(e.data.action)); }
      else if (e.data.type === "quiet") { forceQuiet = !!e.data.on; lastInput = 0; applyLevel(true); }
      else if (e.data.type === "rotate") { rotatePreview = !!e.data.on; lastInput = 0; pageShownAt = Date.now(); }
    });
  }

  setFocus(0);
  paintTime(true);
  showStrip(); // the first page comes with the shell, not through load()
  applyLevel(true);
  tick();
  // Notify's editor reloads the preview with ?saver=1 after a screensaver change.
  if (cfg.preview && /[?&]saver=1(&|$)/.test(location.search)) showSaver();
})();
