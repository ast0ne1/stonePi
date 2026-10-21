/* Displays → Car Thing → Setup: the device status line, the Spotify revoked warning, and the
   one Save bar (it shows once the PIN, client ID or expiry date is changed). */
(() => {
  // /displays/carthing/setup, with or without the /notify prefix.
  const prefix = window.location.pathname.replace(/\/displays\/carthing\/setup\/?$/, "");
  const api = (path) => `${prefix}/api/carthing/${path}`;

  // ── one Save: its fields sit in the Device and Music cards (form="ct-setup") ──
  const form = document.getElementById("ct-setup");
  if (form) {
    const text = form.querySelector("[data-ct-savebar-text]");
    const fields = Array.from(form.elements).filter((el) => el.name && el.type !== "hidden" && el.type !== "submit" && el.type !== "reset");
    const initial = new Map(fields.map((el) => [el, el.type === "checkbox" ? el.checked : el.value]));
    const dirty = () => fields.some((el) => (el.type === "checkbox" ? el.checked : el.value) !== initial.get(el));
    const render = () => {
      const on = dirty();
      form.classList.toggle("is-dirty", on);
      text.textContent = on ? "Unsaved changes" : "Save the PIN, client ID and expiry date";
    };
    fields.forEach((el) => {
      el.addEventListener("input", render);
      el.addEventListener("change", render);
    });
    form.addEventListener("reset", () => setTimeout(render, 0));
    render();
    if (typeof guardLeave === "function") guardLeave(dirty, { body: "Your setup changes aren't saved.", scope: form });
  }

  // ── device status, and the Music card turning red when the sign-in is gone ──
  const statusText = (s) => {
    let text;
    if (!s.service) text = s.enabled ? "the Car Thing service isn't running" : "service stopped";
    else if (!s.adb) text = "ADB isn't installed on this machine";
    else if (!(s.devices || []).length) text = "no Car Thing plugged in";
    else if (s.connected) text = "Car Thing connected and showing the panel";
    else if ((s.devices || []).some((d) => d.injected)) text = "Car Thing found, waiting for it to load the panel";
    else text = "Car Thing found";
    if (s.error) text += ` (${s.error})`;
    return text;
  };
  const statusEl = document.querySelector("[data-ct-status-detail]");
  const expiryEl = document.querySelector("[data-ct-expiry]");
  const expiryClass = expiryEl?.className;
  const showRevoked = (revoked) => {
    if (!expiryEl) return;
    expiryEl.className = revoked ? "ct-expiry is-revoked" : expiryClass;
    expiryEl.setAttribute("role", revoked || expiryClass.includes("is-expired") ? "alert" : "status");
    expiryEl.querySelectorAll(":scope > p").forEach((p) => {
      p.hidden = p.hasAttribute("data-ct-expiry-revoked") ? !revoked : revoked;
    });
  };
  const pollStatus = async () => {
    try {
      const res = await fetch(api("status"));
      const s = await res.json();
      if (statusEl) statusEl.textContent = statusText(s);
      showRevoked((s.music || {}).error_code === "invalid_grant");
    } catch {
      if (statusEl) statusEl.textContent = "status unavailable";
    }
  };
  if (statusEl || expiryEl) {
    pollStatus();
    setInterval(pollStatus, 10000);
  }
})();
