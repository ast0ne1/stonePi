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
    // Screensaver clock, or a Clock & weather widget on the page on screen.
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
    return load("v/app/" + id, false);
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
      case "brightness_up": bright = Math.min(1, bright + 0.2); return applyLevel(true);
      case "brightness_down": bright = Math.max(0.2, bright - 0.2); return applyLevel(true);
      case "restart": return openApp("system");
    }
  }

  // ── Idle, screensaver, dimming ─────────────────────────────────────────
  var mode = "active";
  var lastInput = Date.now();
  var loadedAt = Date.now();
  var bright = 1;
  var forceQuiet = false;
  var backlight = false;
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
  function setPalette(f, quiet) {
    var clock = quiet && cfg.clock.quiet_color ? cfg.clock.quiet_color : cfg.clock.color;
    if (f >= 0.999 && !(quiet && cfg.clock.quiet_color)) {
      body.classList.remove("dim");
      for (var k in DIM_BASE) body.style.removeProperty(k);
      body.style.setProperty("--accent", cfg.look.accent);
      body.style.setProperty("--clock", cfg.clock.color);
      body.style.setProperty("--clock-accent", cfg.clock.accent);
      return;
    }
    body.classList.add("dim");
    for (var key in DIM_BASE) body.style.setProperty(key, hexScale(DIM_BASE[key], f));
    body.style.setProperty("--accent", hexScale(cfg.look.accent, f));
    body.style.setProperty("--clock", hexScale(clock, f));
    body.style.setProperty("--clock-accent", hexScale(cfg.clock.accent, f));
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
  }

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
      applyLevel(true);
    }).catch(function () {});
  }
  function refreshSaver() {
    fetch("v/saver", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : null; }).then(function (html) {
      if (html !== null && mode === "saver") { saver.innerHTML = html; paintTime(true); }
    }).catch(function () {});
  }
  function wake() {
    mode = "active";
    saver.hidden = true;
    view.hidden = false;
    paintTime(true);
    applyLevel(true);
  }

  function onInput(id) {
    lastInput = Date.now();
    if (mode !== "active") { wake(); return; }
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
    toast(network ? "StonePi isn’t answering." : "Panel error: " + msg);
    report(what + (network ? " (network): " : ": ") + msg + (err && err.stack ? " | " + err.stack : ""));
  }
  window.addEventListener("error", function (e) {
    report("onerror: " + e.message + " @ " + e.filename + ":" + e.lineno + ":" + e.colno);
  });
  window.addEventListener("unhandledrejection", function (e) {
    report("unhandled: " + String(e.reason && e.reason.message || e.reason));
  });

  // ── Check in with the Pi every 15 s ────────────────────────────────────
  var frev = "";
  function tick() {
    fetch("tick", { credentials: "same-origin" }).then(function (r) {
      if (r.status === 403) { location.reload(); return null; }
      return r.ok ? r.json() : null;
    }).then(function (t) {
      if (!t) return;
      clockOffset = t.now_ms - Date.now();
      tzMin = t.tz_min;
      if (t.rev && t.rev !== cfg.rev) { location.reload(); return; }
      if (backlight !== !!t.backlight) { backlight = !!t.backlight; applyLevel(true); }
      if (frev && t.frev !== frev) {
        if (mode === "saver") refreshSaver();
        else if (screenKind() === "home" || screenKind() === "app") load(current, false, focusIndex);
      }
      frev = t.frev;
    }).catch(function () {});
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
  setInterval(tick, 15000);

  // Notify's editor drives the preview (buttons on the drawing, "show screensaver", ...).
  if (cfg.preview) {
    window.addEventListener("message", function (e) {
      if (e.source !== window.parent || !e.data || e.data.source !== "stonepi-carthing") return;
      if (e.data.type === "input") onInput(String(e.data.id));
      else if (e.data.type === "saver") { lastInput = Date.now(); showSaver(); }
      else if (e.data.type === "wake") { lastInput = Date.now(); wake(); }
      else if (e.data.type === "quiet") { forceQuiet = !!e.data.on; lastInput = 0; applyLevel(true); }
      else if (e.data.type === "rotate") { rotatePreview = !!e.data.on; lastInput = 0; pageShownAt = Date.now(); }
    });
  }

  setFocus(0);
  paintTime(true);
  applyLevel(true);
  tick();
})();
