/* Sidekick: Preview plays a sample of the picked voice (if it's on the Pi already). */
(() => {
  const pick = document.querySelector("[data-voice-pick]");
  const button = document.querySelector("[data-voice-preview]");
  const audio = document.querySelector("[data-voice-audio]");
  const note = document.querySelector("[data-voice-note]");
  if (!pick || !button || !audio) return;
  const prefix = (document.documentElement.dataset.stonepiPrefix || "").replace(/\/$/, "");
  const label = button.textContent;

  pick.addEventListener("change", () => {
    if (note) note.hidden = true;
  });
  button.addEventListener("click", () => {
    const option = pick.selectedOptions[0];
    if (!option || !option.dataset.installed) {
      if (note) note.hidden = false;
      return;
    }
    if (note) note.hidden = true;
    button.disabled = true;
    button.textContent = "Loading…";
    audio.src = `${prefix}/sidekick/voice-sample?voice=${encodeURIComponent(pick.value)}`;
    const done = () => {
      button.disabled = false;
      button.textContent = label;
    };
    audio.onplaying = done;
    audio.onerror = () => {
      done();
      if (note) {
        note.textContent = "Couldn't play a sample right now.";
        note.hidden = false;
      }
    };
    audio.play().catch(() => audio.onerror());
  });
})();

/* Sidekick tabs: Pebbles · Voice & quiet hours · Activity. Without JS every pane shows.
   A redirect's #anchor (#voice, #quiet, #pebble-3, #add) opens the tab it lives in. */
(() => {
  const bar = document.querySelector("[data-sk-tabs]");
  if (!bar) return;
  const tabs = bar.querySelectorAll("[data-sk-tab]");
  const panes = document.querySelectorAll("[data-sk-pane]");
  const KEY = "sidekick-tab";
  const show = (name, remember) => {
    tabs.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.skTab === name)));
    panes.forEach((p) => (p.hidden = p.dataset.skPane !== name));
    if (remember) {
      try { sessionStorage.setItem(KEY, name); } catch (_) { /* private mode */ }
    }
  };
  const target = location.hash ? document.getElementById(location.hash.slice(1)) : null;
  const home = target && target.closest("[data-sk-pane]");
  let start = home ? home.dataset.skPane : "";
  if (!start) {
    try { start = sessionStorage.getItem(KEY) || ""; } catch (_) { start = ""; }
  }
  if (![...tabs].some((b) => b.dataset.skTab === start)) start = "pebbles";
  bar.hidden = false;
  show(start, false);
  if (target) {
    if (target.tagName === "DETAILS") target.open = true;
    target.scrollIntoView({ block: "start" });
  }
  tabs.forEach((b) => b.addEventListener("click", () => show(b.dataset.skTab, true)));
})();

/* After Add Pebble: the device finishes pairing on its next poll. Watch for it, then reload
   onto its card, so nobody has to refresh by hand. Gives up (with a hint) after 90 s. */
(() => {
  const box = document.querySelector("[data-sk-pairing]");
  if (!box) return;
  const prefix = (document.documentElement.dataset.stonepiPrefix || "").replace(/\/$/, "");
  const known = new Set((box.dataset.known || "").split(",").filter(Boolean));
  const started = Date.now();
  const tick = async () => {
    try {
      const res = await fetch(`${prefix}/sidekick/devices.json`, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const fresh = (data.devices || []).find((d) => !known.has(String(d.id)));
        if (fresh) {
          const msg = encodeURIComponent(`${fresh.nickname} is paired.`);
          location.replace(`${prefix}/sidekick?msg=${msg}#pebble-${fresh.id}`);
          return;
        }
      }
    } catch (_) { /* Notify restarting: try again */ }
    if (Date.now() - started > 90000) {
      const late = box.querySelector("[data-sk-pairing-late]");
      if (late) late.hidden = false;
      return;
    }
    setTimeout(tick, 2500);
  };
  setTimeout(tick, 1500);
})();

/* Set up speech: while the background setup runs, show its step and reload when it ends
   (the Ready card then shows Listening and Speaking, or what went wrong). */
(() => {
  const box = document.querySelector("[data-sk-speech]");
  if (!box) return;
  const prefix = (document.documentElement.dataset.stonepiPrefix || "").replace(/\/$/, "");
  const step = box.querySelector("[data-sk-speech-step]");
  const tick = async () => {
    try {
      const r = await fetch(`${prefix}/sidekick/speech-setup.json`, { headers: { Accept: "application/json" } });
      const data = await r.json();
      if (data.ok && data.state !== "running") {
        location.replace(`${prefix}/sidekick?speech=done#ready`);  // a new query, so it loads afresh
        return;
      }
      if (step && data.step) step.textContent = `: ${data.step}`;
    } catch (_) { /* Notify restarting: try again */ }
    setTimeout(tick, 5000);
  };
  setTimeout(tick, 5000);
})();
