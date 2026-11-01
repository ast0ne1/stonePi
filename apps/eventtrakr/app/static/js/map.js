/* EventTrakr map view (F18, ideation/eventtrakr-map.md).
 *
 * One calendar week is loaded at a time (/api/map/week); everything else --
 * which pins are in view, the day bars, filters -- is worked out here, so
 * panning and zooming never fetch. Times are stored as local wall-clock in
 * UTC fields, so all day maths below uses the UTC getters. */
(function () {
  "use strict";

  var page = document.querySelector("[data-map-page]");
  if (!page || !window.L) return;

  var LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
  var DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var COMMITTEE = "Committee meetings";
  var VIEW_KEY = "eventtrakr-map-view";
  var HOME = [55.6761, 12.5683]; // Copenhagen, until the week's events say otherwise

  function withPrefix(path) {
    var prefix = (document.documentElement.getAttribute("data-stonepi-prefix") || "").replace(/\/$/, "");
    if (!prefix || path.charAt(0) !== "/" || path.indexOf("//") === 0) return path;
    if (path === prefix || path.indexOf(prefix + "/") === 0) return path;
    return prefix + path;
  }

  function iconSvg(name) {
    var tpl = document.querySelector('template[data-map-icon="' + name + '"]') ||
      document.querySelector('template[data-map-icon="tag"]');
    return tpl ? tpl.innerHTML.trim() : "";
  }

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toast(message) {
    var el = document.querySelector("[data-toast]");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { el.hidden = true; }, 2600);
  }

  function readJSON(key) {
    try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }

  function writeJSON(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }

  function dayStart(iso) {
    var d = new Date(iso + "T00:00:00Z");
    return d.getTime();
  }

  var DAY_MS = 86400000;

  function parseTime(iso) { return iso ? new Date(iso).getTime() : null; }

  function hhmm(ms) {
    var d = new Date(ms);
    var h = d.getUTCHours(), m = d.getUTCMinutes();
    if (h === 0 && m === 0) return "";
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function shortDate(ms) {
    var d = new Date(ms);
    return DAY_NAMES[(d.getUTCDay() + 6) % 7] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
  }

  // -- state --
  var state = {
    week: null,
    today: null,
    syncedUntil: null,
    events: [],
    day: null,          // 0..6 within the week, or null for the whole week
    off: {},            // category name -> true when hidden
    free: false,
    place: null,        // place_id the cards are narrowed to
    unplacedOnly: false,
    searching: false,
    locating: false,
    moving: null,       // place_id whose pin is being dragged
  };
  var committeeDefaulted = false;

  // -- map --
  var saved = readJSON(VIEW_KEY);
  var map = L.map(page.querySelector("[data-map-canvas]"), {
    zoomControl: true,
    attributionControl: true,
  }).setView(saved ? [saved.lat, saved.lon] : HOME, saved ? saved.zoom : 13);

  function isDark() {
    var theme = document.documentElement.getAttribute("data-theme");
    if (theme) return theme === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // OpenStreetMap's own tiles (fine for one household's use, no key); in dark
  // mode they're darkened with a CSS filter rather than a second tile set.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    // OSM's tile policy needs a Referer, and StonePi's nginx sends
    // Referrer-Policy: same-origin (no Referer to other sites) for every page.
    // Tiles alone send the site's origin, e.g. "http://stonepi.local/".
    referrerPolicy: "strict-origin-when-cross-origin",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  function setTheme() { page.classList.toggle("is-dark", isDark()); }
  setTheme();
  new MutationObserver(setTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", setTheme);

  var pinLayer = L.layerGroup().addTo(map);
  var hadSavedView = !!saved;

  // -- elements --
  var tray = page.querySelector("[data-map-tray]");
  var weekCard = page.querySelector("[data-map-week]");
  var labelEl = page.querySelector("[data-week-label]");
  var prevBtn = page.querySelector("[data-week-prev]");
  var nextBtn = page.querySelector("[data-week-next]");
  var catsEl = page.querySelector("[data-map-cats]");
  var barsEl = page.querySelector("[data-map-bars]");
  var countEl = page.querySelector("[data-week-count]");
  var furtherBtn = page.querySelector("[data-look-further]");
  var freeChip = page.querySelector("[data-free-chip]");
  var unplacedChip = page.querySelector("[data-unplaced-chip]");
  var noteEl = page.querySelector("[data-week-note]");
  var toggleBtn = page.querySelector("[data-week-toggle]");
  var bodyEl = page.querySelector("[data-week-body]");
  var summaryEl = page.querySelector("[data-week-summary]");
  var COLLAPSED_KEY = "eventtrakr-map-week-hidden";

  var dock = page.querySelector("[data-map-dock]");
  function syncTrayHeight() {
    // "Show where I am" sits just above the dock, however tall it is now.
    page.style.setProperty("--map-dock-height", dock.getBoundingClientRect().height + "px");
  }

  // -- filters --
  function weekStartMs() { return dayStart(state.week.start); }

  function onDay(ev, i) {
    var from = weekStartMs() + i * DAY_MS, to = from + DAY_MS;
    var start = parseTime(ev.start);
    // A one-off belongs to the day it starts (a club night ending at 04:00
    // isn't on the next day); only a run covers every day until its end.
    var end = ev.is_run ? parseTime(ev.end) : start;
    return start < to && end >= from;
  }

  function passesFilters(ev) {
    if (state.off[ev.category]) return false;
    if (state.free && !ev.free) return false;
    return true;
  }

  function inView(ev, bounds) {
    return ev.lat != null && bounds.contains([ev.lat, ev.lon]);
  }

  function shownEvents() {
    // On the map: passes filters, on the chosen day (or any day this week).
    return state.events.filter(function (ev) {
      if (!passesFilters(ev)) return false;
      if (state.day == null) return true;
      return onDay(ev, state.day);
    });
  }

  // -- pins --
  function placeGroups(events) {
    var groups = {};
    events.forEach(function (ev) {
      if (ev.lat == null) return;
      var key = ev.place_id;
      (groups[key] = groups[key] || { place_id: key, lat: ev.lat, lon: ev.lon, events: [] }).events.push(ev);
    });
    return Object.keys(groups).map(function (k) { return groups[k]; });
  }

  function mainEvent(group) {
    // The pin wears the most common category's icon at that venue.
    var counts = {}, best = group.events[0];
    group.events.forEach(function (ev) {
      counts[ev.category] = (counts[ev.category] || 0) + 1;
      if (counts[ev.category] > counts[best.category]) best = ev;
    });
    return best;
  }

  function drawPins() {
    pinLayer.clearLayers();
    placeGroups(shownEvents()).forEach(function (group) {
      var ev = mainEvent(group);
      var selected = state.place === group.place_id;
      var moving = state.moving === group.place_id;
      var html =
        '<div class="map-pin' + (selected ? " is-selected" : "") + (moving ? " is-moving" : "") +
        '" style="--pin-fg: var(--tint-' + esc(ev.tint) + '-fg)">' + iconSvg(ev.icon) +
        (group.events.length > 1 ? '<span class="map-pin-count">' + group.events.length + "</span>" : "") +
        (selected ? '<span class="map-pin-label">' + esc(ev.location) + "</span>" : "") +
        "</div>";
      var marker = L.marker([group.lat, group.lon], {
        icon: L.divIcon({ className: "map-pin-wrap", html: html, iconSize: [40, 47], iconAnchor: [20, 47] }),
        draggable: moving,
        keyboard: true,
        title: ev.location,
        zIndexOffset: selected ? 1000 : 0,
      });
      marker.on("click", function () {
        state.place = state.place === group.place_id ? null : group.place_id;
        state.unplacedOnly = false;
        render();
        if (state.place != null) scrollToCards();
      });
      if (moving) {
        marker.on("dragend", function () {
          var pos = marker.getLatLng();
          savePlace(group.place_id, pos.lat, pos.lng);
        });
      }
      marker.addTo(pinLayer);
    });
  }

  // -- the week card --
  function drawCats() {
    var seen = {};
    state.events.forEach(function (ev) {
      if (!seen[ev.category]) seen[ev.category] = ev;
    });
    var names = Object.keys(seen).sort(function (a, b) {
      return (a === COMMITTEE) - (b === COMMITTEE) || a.localeCompare(b);
    });
    if (!committeeDefaulted && seen[COMMITTEE]) {
      // Committee meetings start hidden (spec §2), in whichever week they
      // first turn up; after that the toggle is the viewer's.
      state.off[COMMITTEE] = true;
      committeeDefaulted = true;
    }
    var allOn = names.every(function (n) { return !state.off[n]; });
    var html = '<button type="button" class="map-cat" data-cat-all aria-pressed="' + allOn + '" title="All categories" aria-label="All categories">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"></rect><rect x="14" y="3" width="7" height="7" rx="1.5"></rect><rect x="3" y="14" width="7" height="7" rx="1.5"></rect><rect x="14" y="14" width="7" height="7" rx="1.5"></rect></svg></button>';
    names.forEach(function (name) {
      var ev = seen[name];
      html += '<button type="button" class="map-cat" data-cat="' + esc(name) + '" aria-pressed="' + !state.off[name] +
        '" title="' + esc(name) + '" aria-label="' + esc(name) + '" style="--cat-fg: var(--tint-' + esc(ev.tint) + '-fg)">' +
        iconSvg(ev.icon) + "</button>";
    });
    catsEl.innerHTML = html;
  }

  function drawBars() {
    var bounds = map.getBounds();
    var counts = [];
    for (var i = 0; i < 7; i++) counts.push({ solid: 0, run: 0 });
    state.events.forEach(function (ev) {
      if (!passesFilters(ev) || !inView(ev, bounds)) return;
      for (var d = 0; d < 7; d++) {
        if (onDay(ev, d)) counts[d][ev.is_run ? "run" : "solid"] += 1;
      }
    });
    var max = Math.max.apply(null, counts.map(function (c) { return c.solid + c.run; }).concat([1]));
    var todayMs = dayStart(state.today);
    var syncedMs = dayStart(state.syncedUntil);
    var html = "";
    for (var d = 0; d < 7; d++) {
      var ms = weekStartMs() + d * DAY_MS;
      var past = ms < todayMs;
      var unsynced = ms > syncedMs;
      var c = counts[d];
      var total = c.solid + c.run;
      var classes = "map-day" + (state.day === d ? " is-selected" : "") + (ms === todayMs ? " is-today" : "") + (unsynced ? " is-unsynced" : "");
      var label = shortDate(ms) + ": " + (past ? "already gone" : total + " event" + (total === 1 ? "" : "s") + (c.run ? " (" + c.run + " running all day)" : "") + (unsynced ? ", not synced yet" : ""));
      html += '<button type="button" class="' + classes + '" data-day="' + d + '"' + (past ? " disabled" : "") +
        ' aria-pressed="' + (state.day === d) + '" title="' + esc(label) + '" aria-label="' + esc(label) + '">' +
        '<span class="map-day-track">' +
        (total ? '<span class="map-day-solid" style="height:' + (56 * c.solid / max) + 'px"></span><span class="map-day-run" style="height:' + (56 * c.run / max) + 'px"></span>' : '<span class="map-day-empty"></span>') +
        "</span>" +
        '<span class="map-day-letter">' + LETTERS[d] + "</span>" +
        '<span class="map-day-date">' + new Date(ms).getUTCDate() + "</span></button>";
    }
    barsEl.innerHTML = html;
    drawMiniBars(counts, max, todayMs);
    return counts;
  }

  // The collapsed tab's version of the bars: one short row per day, M at the top.
  function drawMiniBars(counts, max, todayMs) {
    var html = "";
    for (var d = 0; d < 7; d++) {
      var c = counts[d], total = c.solid + c.run;
      var past = weekStartMs() + d * DAY_MS < todayMs;
      html += '<span class="map-mini-day' + (state.day === d ? " is-selected" : "") + (past ? " is-past" : "") + '">' +
        '<span class="map-mini-letter">' + LETTERS[d] + "</span>" +
        '<span class="map-mini-bar" style="width:' + (total ? Math.max(3, Math.round(22 * total / max)) : 2) + 'px"></span></span>';
    }
    page.querySelector("[data-mini-bars]").innerHTML = html;
  }

  function dayLabel(d) {
    var ms = weekStartMs() + d * DAY_MS, todayMs = dayStart(state.today);
    if (ms === todayMs) return "Today";
    if (ms === todayMs + DAY_MS) return "Tomorrow";
    return shortDate(ms);
  }

  function drawWeekText(counts) {
    labelEl.textContent = state.week.label;
    labelEl.classList.toggle("is-current", !!state.week.is_current);
    prevBtn.disabled = !state.week.prev;
    nextBtn.disabled = !state.week.next;

    var n;
    if (state.day == null) {
      n = 0;
      var bounds = map.getBounds();
      state.events.forEach(function (ev) { if (passesFilters(ev) && inView(ev, bounds)) n += 1; });
    } else {
      n = counts[state.day].solid + counts[state.day].run;
    }
    countEl.textContent = (state.day == null ? "All week" : dayLabel(state.day)) + " · " + n + " event" + (n === 1 ? "" : "s");
    // The collapsed tab: a week pill and a count pill (the full line for screen readers).
    summaryEl.querySelector("[data-pill-week]").textContent = "Wk " + state.week.number;
    summaryEl.querySelector("[data-pill-count]").textContent = String(n);
    var summary = "Week " + state.week.number + ", " + countEl.textContent + ". Show the week and filters";
    summaryEl.setAttribute("aria-label", summary);
    summaryEl.title = summary;

    // Days past the synced window: runs and further dates already known show;
    // new one-off events there only come from a deep search, on request.
    var lastDay = weekStartMs() + 6 * DAY_MS;
    var hasUnsynced = lastDay > dayStart(state.syncedUntil);
    furtherBtn.hidden = !hasUnsynced;
    furtherBtn.disabled = state.searching;
    furtherBtn.textContent = state.searching ? "Looking further ahead…" : "Look for more in wk " + state.week.number + " ›";

    var unplaced = state.events.filter(function (ev) { return ev.lat == null && passesFilters(ev) && (state.day == null || onDay(ev, state.day)); });
    unplacedChip.hidden = !unplaced.length;
    unplacedChip.textContent = unplaced.length + " not on the map";
    unplacedChip.setAttribute("aria-pressed", String(state.unplacedOnly));
    freeChip.setAttribute("aria-pressed", String(state.free));
    noteEl.hidden = !state.locating;
    noteEl.textContent = state.locating ? "Finding venues…" : "";
  }

  // -- event cards --
  function cardEvents() {
    var bounds = map.getBounds();
    return shownEvents().filter(function (ev) {
      if (state.unplacedOnly) return ev.lat == null;
      if (state.place != null) return ev.place_id === state.place;
      return inView(ev, bounds);
    }).slice(0, 40);
  }

  function cardHtml(ev) {
    var start = parseTime(ev.start);
    var when = ev.is_run
      ? shortDate(start) + " – until " + shortDate(parseTime(ev.end))
      : shortDate(start) + (hhmm(start) ? ", " + hhmm(start) : "");
    var cost = '<span class="badge cost-pill ' + esc(ev.cost_class) + (ev.cost_text ? "" : " is-icon-only") + '" title="' + esc(ev.cost_title) + '" aria-label="' + esc(ev.cost_title) + '" role="img">' + iconSvg("tag") + (ev.cost_text ? '<span aria-hidden="true">' + esc(ev.cost_text) + "</span>" : "") + "</span>";
    return '<article class="map-card" data-card="' + ev.id + '">' +
      '<div class="map-card-top"><span class="badge category tint-' + esc(ev.tint) + '">' + iconSvg(ev.icon) + "<span>" + esc(ev.category) + "</span></span>" + cost +
      '<button type="button" class="btn-star' + (ev.favourited ? " starred" : "") + '" data-map-fav="' + ev.id + '" title="' + (ev.favourited ? "Favourited" : "Add to Favourites") + '">' + iconSvg(ev.favourited ? "star-filled" : "star") + "</button></div>" +
      '<h2 class="map-card-title">' + esc(ev.title) + "</h2>" +
      '<div class="map-card-line">' + iconSvg("clock") + "<span>" + esc(when) + "</span></div>" +
      '<div class="map-card-line">' + iconSvg("map-pin") + "<span>" + esc(ev.location) + "</span></div>" +
      (ev.also_on.length ? '<div class="map-card-line"><span>' + esc(ev.source) + " · also on " + esc(ev.also_on.join(", ")) + "</span></div>" : (ev.source ? '<div class="map-card-line"><span>' + esc(ev.source) + "</span></div>" : "")) +
      '<div class="map-card-foot">' +
      (ev.place_id != null && ev.lat != null ? '<button type="button" class="map-week-link" data-move-place="' + ev.place_id + '">' + (state.moving === ev.place_id ? "Drag the pin, or cancel" : "Wrong place?") + "</button>" : "") +
      (ev.url ? '<a class="btn btn-outline btn-sm btn-icon" href="' + esc(ev.url) + '" target="_blank" rel="noopener noreferrer" aria-label="Open event details" title="Open event details">' + iconSvg("external-link") + "</a>" : "") +
      "</div></article>";
  }

  // The tray's pages: one per event (the week card stays put beside them).
  function trayPages() { return Array.prototype.slice.call(tray.children); }

  function pageAt() {
    // The page the tray is showing (the one nearest its left edge).
    var best = 0;
    trayPages().forEach(function (el, i) {
      if (Math.abs(el.offsetLeft - tray.scrollLeft) < Math.abs(trayPages()[best].offsetLeft - tray.scrollLeft)) best = i;
    });
    return best;
  }

  function goToPage(i, smooth) {
    var pages = trayPages();
    var el = pages[Math.max(0, Math.min(i, pages.length - 1))];
    if (el) tray.scrollTo({ left: el.offsetLeft, behavior: smooth ? "smooth" : "auto" });
  }

  var lastCardsKey = null;
  function drawCards() {
    var list = cardEvents();
    // Redraws happen on every pan and while venues are being looked up: only
    // rebuild when the cards would change, and stay on the card being read.
    var key = list.map(function (ev) { return ev.id + (ev.favourited ? "*" : "") + (state.moving === ev.place_id ? "m" : ""); }).join(",");
    if (key === lastCardsKey) return;
    lastCardsKey = key;
    var anchor = tray.scrollLeft > 20 ? (trayPages()[pageAt()] || tray).getAttribute("data-card") : null;
    Array.prototype.forEach.call(tray.querySelectorAll(".map-card"), function (el) { el.remove(); });
    tray.insertAdjacentHTML("beforeend", list.map(cardHtml).join(""));
    if (anchor) {
      var again = tray.querySelector('[data-card="' + anchor + '"]');
      tray.scrollLeft = again ? again.offsetLeft : 0;
    }
  }

  function scrollToCards() {
    goToPage(0, true);
  }

  // A mouse wheel (or a trackpad's up/down) moves one card at a time; touch
  // screens swipe as usual.
  var wheelLock = 0;
  tray.addEventListener("wheel", function (e) {
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return; // a sideways trackpad swipe scrolls natively
    e.preventDefault();
    var now = Date.now();
    if (now < wheelLock) return;
    wheelLock = now + 350;
    goToPage(pageAt() + (e.deltaY > 0 ? 1 : -1), true);
  }, { passive: false });

  function render() {
    if (!state.week) return;
    drawCats();
    var counts = drawBars();
    drawWeekText(counts);
    drawPins();
    drawCards();
    syncTrayHeight();
  }

  // -- loading a week --
  var pollTimer = null;
  function load(start, opts) {
    opts = opts || {};
    var url = "/api/map/week" + (start ? "?start=" + encodeURIComponent(start) : "");
    return fetch(withPrefix(url), { headers: { Accept: "application/json" } })
      .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (data) {
        var changedWeek = !state.week || state.week.start !== data.week.start;
        state.week = data.week;
        state.today = data.today;
        state.syncedUntil = data.synced_until;
        state.events = data.events;
        state.searching = data.searching;
        state.locating = data.locating;
        if (changedWeek) {
          state.place = null;
          state.unplacedOnly = false;
          // This week opens on today; another week on the whole week.
          state.day = data.week.is_current ? Math.round((dayStart(data.today) - dayStart(data.week.start)) / DAY_MS) : null;
        }
        if (!hadSavedView && !opts.keepView) {
          var located = data.events.filter(function (ev) { return ev.lat != null; });
          if (located.length) map.fitBounds(located.map(function (ev) { return [ev.lat, ev.lon]; }), { padding: [40, 40], maxZoom: 15 });
          hadSavedView = true;
        }
        render();
        clearTimeout(pollTimer);
        if (state.searching || state.locating) {
          pollTimer = setTimeout(function () { load(state.week.start, { keepView: true }); }, state.searching ? 5000 : 15000);
        }
      })
      .catch(function () { toast("Couldn't load the map's events"); });
  }

  // -- interactions --
  map.on("moveend", function () {
    var c = map.getCenter();
    writeJSON(VIEW_KEY, { lat: c.lat, lon: c.lng, zoom: map.getZoom() });
    render();
  });
  map.on("click", function () {
    if (state.place != null) {
      state.place = null;
      render();
    }
  });

  prevBtn.addEventListener("click", function () { if (state.week.prev) load(state.week.prev, { keepView: true }); });
  nextBtn.addEventListener("click", function () { if (state.week.next) load(state.week.next, { keepView: true }); });

  barsEl.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-day]");
    if (!btn || btn.disabled) return;
    var d = Number(btn.getAttribute("data-day"));
    state.day = state.day === d ? null : d;
    render();
  });

  catsEl.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-cat], [data-cat-all]");
    if (!btn) return;
    committeeDefaulted = true;
    if (btn.hasAttribute("data-cat-all")) {
      state.off = {};
    } else {
      var name = btn.getAttribute("data-cat");
      state.off[name] = !state.off[name];
    }
    render();
  });

  freeChip.addEventListener("click", function () { state.free = !state.free; render(); });
  unplacedChip.addEventListener("click", function () {
    state.unplacedOnly = !state.unplacedOnly;
    state.place = null;
    render();
    if (state.unplacedOnly) scrollToCards();
  });

  furtherBtn.addEventListener("click", function () {
    state.searching = true;
    render();
    fetch(withPrefix("/api/map/look-further"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: state.week.start }),
    })
      .then(function () { setTimeout(function () { load(state.week.start, { keepView: true }); }, 4000); })
      .catch(function () { state.searching = false; render(); toast("Couldn't start the search"); });
  });

  tray.addEventListener("click", function (e) {
    var fav = e.target.closest("[data-map-fav]");
    if (fav) {
      var id = Number(fav.getAttribute("data-map-fav"));
      fav.disabled = true;
      fetch(withPrefix("/api/events/" + id + "/favourite"), { method: "POST", headers: { "Content-Type": "application/json" } })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          state.events.forEach(function (ev) { if (ev.id === id) ev.favourited = !!data.favourited; });
          toast(data.favourited ? "⭐ Added to Favourites" : "Removed from Favourites");
          render();
        })
        .catch(function () { fav.disabled = false; toast("Could not update favourite"); });
      return;
    }
    var move = e.target.closest("[data-move-place]");
    if (move) {
      var placeId = Number(move.getAttribute("data-move-place"));
      state.moving = state.moving === placeId ? null : placeId;
      state.place = placeId;
      render();
      if (state.moving != null) toast("Drag the pin to where it really is");
    }
  });

  function savePlace(placeId, lat, lon) {
    fetch(withPrefix("/api/map/places/" + placeId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: lat, lon: lon }),
    })
      .then(function (res) { if (!res.ok) throw new Error(); })
      .then(function () {
        state.events.forEach(function (ev) { if (ev.place_id === placeId) { ev.lat = lat; ev.lon = lon; } });
        state.moving = null;
        toast("Saved: every event there has moved");
        render();
      })
      .catch(function () { toast("Couldn't save the new position"); });
  }

  // -- find a place --
  var findForm = page.querySelector("[data-map-find]");
  var findInput = page.querySelector("[data-map-find-input]");
  var findResults = page.querySelector("[data-map-find-results]");
  findForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = findInput.value.trim();
    if (!q) return;
    findResults.hidden = false;
    findResults.innerHTML = '<li class="is-note">Searching…</li>';
    fetch(withPrefix("/api/map/search?q=" + encodeURIComponent(q)))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data.results || !data.results.length) {
          findResults.innerHTML = '<li class="is-note">' + esc(data.error || "Nothing found") + "</li>";
          return;
        }
        findResults.innerHTML = data.results.map(function (r, i) {
          return '<li><button type="button" data-find="' + i + '">' + esc(r.name) + "</button></li>";
        }).join("");
        findResults.onclick = function (ev) {
          var btn = ev.target.closest("[data-find]");
          if (!btn) return;
          var r = data.results[Number(btn.getAttribute("data-find"))];
          map.setView([r.lat, r.lon], 15);
          findResults.hidden = true;
          findInput.blur();
        };
      })
      .catch(function () { findResults.innerHTML = '<li class="is-note">Place search isn\'t available right now</li>'; });
  });
  document.addEventListener("click", function (e) {
    if (!findForm.contains(e.target)) findResults.hidden = true;
  });

  // On the map, the header's search button goes to "Find a place" instead of
  // opening the shared search panel (which would sit under the map). Caught
  // before the shared header's own click handler sees it.
  document.addEventListener("click", function (e) {
    var trigger = e.target.closest(".sp-pop-search [data-sp-trigger]");
    if (!trigger) return;
    e.preventDefault();
    e.stopPropagation();
    findInput.focus();
    findInput.select();
  }, true);

  // -- where am I --
  var meMarker = null;
  page.querySelector("[data-map-locate]").addEventListener("click", function () {
    if (!navigator.geolocation) { toast("This browser can't share its location"); return; }
    navigator.geolocation.getCurrentPosition(function (pos) {
      var at = [pos.coords.latitude, pos.coords.longitude];
      if (meMarker) map.removeLayer(meMarker);
      meMarker = L.circleMarker(at, { radius: 8, color: "#fff", weight: 3, fillColor: "#2558C8", fillOpacity: 1 }).addTo(map);
      map.setView(at, Math.max(map.getZoom(), 15));
    }, function () { toast("Couldn't get your location"); }, { enableHighAccuracy: true, timeout: 10000 });
  });

  // -- hiding the week card down to one line (remembered on this device) --
  function setCollapsed(hidden) {
    weekCard.classList.toggle("is-collapsed", hidden);
    bodyEl.hidden = hidden;
    summaryEl.hidden = !hidden;
    toggleBtn.setAttribute("aria-expanded", String(!hidden));
    var label = hidden ? "Show the week and filters" : "Collapse the week and filters";
    toggleBtn.setAttribute("aria-label", label);
    toggleBtn.title = label;
    syncTrayHeight();
  }
  setCollapsed(readJSON(COLLAPSED_KEY) === true);
  summaryEl.addEventListener("click", function () { toggleBtn.click(); });
  toggleBtn.addEventListener("click", function () {
    var hidden = !weekCard.classList.contains("is-collapsed");
    setCollapsed(hidden);
    writeJSON(COLLAPSED_KEY, hidden);
  });

  window.addEventListener("resize", function () { map.invalidateSize(); syncTrayHeight(); });
  load(null);
})();
