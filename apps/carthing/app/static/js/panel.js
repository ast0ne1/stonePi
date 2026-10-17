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

  function focusables() { return view.querySelectorAll("[data-focus]"); }
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

  function load(url, push, focus) {
    loading = true;
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 403) { location.reload(); return null; }
      return r.ok ? r.text() : null;
    }).then(function (html) {
      loading = false;
      if (html === null) { toast("Couldn’t load that."); return; }
      if (push && current !== url) stack.push({ url: current, focus: focusIndex });
      current = url;
      view.innerHTML = html;
      pinDigits = "";
      setFocus(focus || 0);
      paintTime(true);
    }).catch(function (err) { loading = false; fail(url, err); });
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
      if (res.ok) { toast(res.message || "Restarted."); goHome(); return; }
      var msg = view.querySelector("[data-pin-msg]");
      if (msg) { msg.textContent = res.message || "That didn’t work."; msg.classList.add("bad"); pinDigits = ""; paintPin(); }
      else toast(res.message || "That didn’t work.");
    }).catch(function (err) { fail("restart", err); });
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
    var bar = npRoot().querySelector("[data-np-vol] i");
    if (bar) {
      var now = parseFloat(bar.style.width) || 0;
      bar.style.width = Math.max(0, Math.min(100, now + dir * cfg.music.step)) + "%";
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
    nodes = root.querySelectorAll('[data-music="repeat"]');
    for (i = 0; i < nodes.length; i++) { nodes[i].classList.toggle("on", st.repeat !== "off"); nodes[i].setAttribute("data-repeat", st.repeat); }
    var d = st.device;
    nodes = root.querySelectorAll("[data-np-vol]");
    for (i = 0; i < nodes.length; i++) {
      var hasVol = !!(d && d.supports_volume && d.volume !== null);
      nodes[i].hidden = !hasVol;
      if (hasVol && !volClicks) nodes[i].querySelector("i").style.width = d.volume + "%";
    }
    paintBars();
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

  var SAVERS = { logo: logoSaver, matrix: matrixSaver, pipes: pipesSaver };
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
    if (screenKind() === "restart") goHome();
    return fetch("v/saver", { credentials: "same-origin" }).then(function (r) {
      if (r.status === 403) { location.reload(); return null; }
      return r.ok ? r.text() : null;
    }).then(function (html) {
      if (html === null || mode !== "saver") return;
      saver.innerHTML = html;
      saver.hidden = false;
      view.hidden = true;
      paintTime(true);
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
    applyLevel(true);
  }

  function onInput(id) {
    lastInput = Date.now();
    if (mode !== "active") { wake(); return; }
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
    var dot = start && start.closest ? start.closest("[data-page-go]") : null;
    if (dot) return perform("page:" + dot.getAttribute("data-page-go"));
    var el = start && start.closest ? start.closest("[data-focus]") : null;
    if (!el) return;
    var items = focusables();
    for (var i = 0; i < items.length; i++) if (items[i] === el) setFocus(i);
    activate(el);
  });

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
      else load(current, false, focusIndex);
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
        else if (screenKind() === "home" || screenKind() === "app" || screenKind() === "weather") load(current, false, focusIndex);
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
  applyLevel(true);
  tick();
  // Notify's editor reloads the preview with ?saver=1 after a screensaver change.
  if (cfg.preview && /[?&]saver=1(&|$)/.test(location.search)) showSaver();
})();
