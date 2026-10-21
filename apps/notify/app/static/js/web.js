/* Displays → Web pages (F14): capture previews.
   - Web page editor ([data-web-page]): the saved page at 800×480, in colour.
   - TRMNL Web page Display ([data-web-display]): the exact e-ink image a push sends,
     from the form's unsaved settings. */
(() => {
  // Displays list: the Web page card asks for the page (an existing one, or an address).
  const create = document.querySelector("[data-display-create]");
  if (create) {
    const fields = create.querySelector("[data-web-fields]");
    const pick = create.querySelector("[data-page-pick]");
    const newPage = create.querySelector("[data-new-page]");
    const url = newPage.querySelector('input[name="url"]');
    const sync = () => {
      const web = create.querySelector("[data-kind]:checked")?.value === "web";
      fields.hidden = !web;
      const needsUrl = web && !(pick && pick.value);
      newPage.hidden = !needsUrl;
      url.required = needsUrl;
    };
    create.querySelectorAll("[data-kind]").forEach((r) => r.addEventListener("change", sync));
    pick?.addEventListener("change", sync);
    sync();
  }

  const pageRoot = document.querySelector("[data-web-page]");
  const displayRoot = document.querySelector("[data-web-display]");
  const root = pageRoot || displayRoot;
  if (!root) return;

  const csrf = document.querySelector('input[name="csrf_token"]')?.value || "";
  const img = root.querySelector("[data-web-img]");
  const status = root.querySelector("[data-web-status]");
  const buttons = root.querySelectorAll("[data-web-preview], [data-web-preview-fresh]");
  // Relative, so it works with or without the /notify prefix: the editor is at
  // /displays/web/<id>, a Display at /displays/<id>.
  const up = pageRoot ? "../../" : "../";
  const api = (path) => new URL(`${up}api/${path}`, window.location.href).href;
  let objectUrl = "";

  const show = (blob, text) => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(blob);
    img.src = objectUrl;
    img.hidden = false;
    status.textContent = text;
  };

  const failMessage = async (res) => {
    try {
      const body = await res.json();
      return body.message || `HTTP ${res.status}`;
    } catch {
      return `HTTP ${res.status}`;
    }
  };

  const kb = (bytes) => `${(Number(bytes) / 1024).toFixed(1)} KB`;

  const preview = async (fresh) => {
    buttons.forEach((b) => (b.disabled = true));
    status.textContent = "Capturing the page…";
    const started = Date.now();
    try {
      let res;
      if (pageRoot) {
        const id = encodeURIComponent(pageRoot.dataset.webPage);
        res = await fetch(api(`web/${id}/preview.png${fresh ? "?fresh=1" : ""}`), { credentials: "same-origin" });
      } else {
        const form = root.querySelector("[data-web-form]");
        const data = Object.fromEntries(new FormData(form).entries());
        data.dither = form.querySelector('[name="dither"]').checked;
        data.fresh = fresh;
        delete data.csrf_token;
        res = await fetch(api(`displays/${encodeURIComponent(displayRoot.dataset.webDisplay)}/web-preview`), {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-StonePi-CSRF": csrf },
          body: JSON.stringify(data),
        });
      }
      if (!res.ok) throw new Error(await failMessage(res));
      const blob = await res.blob();
      const took = ((Date.now() - started) / 1000).toFixed(1);
      if (pageRoot) {
        show(blob, `Captured in ${took} s · ${kb(blob.size)}`);
      } else {
        const h = res.headers;
        const greys = { 1: "black and white", 2: "4 greys", 4: "16 greys" }[h.get("X-Image-Bits")] || "";
        const inverted = h.get("X-Image-Inverted") === "1" ? " · inverted (dark page)" : "";
        show(blob, `${h.get("X-Image-Size") || ""} · ${greys} · ${kb(h.get("X-Image-Bytes") || blob.size)} of TRMNL's 1 MB${inverted} · ${took} s`);
      }
    } catch (err) {
      status.textContent = err.message || "Couldn't capture the page.";
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  };

  root.querySelector("[data-web-preview]")?.addEventListener("click", () => preview(false));
  root.querySelector("[data-web-preview-fresh]")?.addEventListener("click", () => preview(true));

  const threshold = root.querySelector("[data-web-threshold]");
  const thresholdOut = root.querySelector("[data-web-threshold-out]");
  threshold?.addEventListener("input", () => (thresholdOut.textContent = threshold.value));
})();
