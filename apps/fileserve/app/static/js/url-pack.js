(() => {
  const root = document.querySelector("[data-url-pack]");
  if (!root) return;

  const MAX_ASSETS = 120;
  const MAX_BYTES = 40 * 1024 * 1024;
  const FETCH_PATH = "/admin/add/fetch";

  const els = {
    url: document.getElementById("pack-url"),
    name: document.getElementById("pack-name"),
    go: document.getElementById("pack-go"),
    clear: document.getElementById("pack-clear"),
    log: document.getElementById("pack-log"),
  };

  function log(msg, kind = "") {
    const line = document.createElement("div");
    if (kind) line.className = kind;
    line.textContent = msg;
    if (els.log.querySelector(".muted") && els.log.childNodes.length === 1) els.log.textContent = "";
    els.log.appendChild(line);
    els.log.scrollTop = els.log.scrollHeight;
  }

  els.clear.addEventListener("click", () => {
    els.log.innerHTML = '<span class="muted">Ready.</span>';
  });

  function crcTable() {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  }
  const CRC_TABLE = crcTable();

  function crc32(data) {
    let c = 0xffffffff;
    for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function u16(n) {
    const b = new Uint8Array(2);
    new DataView(b.buffer).setUint16(0, n, true);
    return b;
  }
  function u32(n) {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setUint32(0, n, true);
    return b;
  }

  function concat(chunks) {
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total);
    let o = 0;
    for (const c of chunks) {
      out.set(c, o);
      o += c.length;
    }
    return out;
  }

  function buildZip(files) {
    const localParts = [];
    const centralParts = [];
    let offset = 0;
    const entries = Object.keys(files).sort();
    for (const name of entries) {
      const data = files[name];
      const nameBytes = new TextEncoder().encode(name);
      const crc = crc32(data);
      const local = concat([
        u32(0x04034b50),
        u16(20),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(crc),
        u32(data.length),
        u32(data.length),
        u16(nameBytes.length),
        u16(0),
        nameBytes,
        data,
      ]);
      const central = concat([
        u32(0x02014b50),
        u16(20),
        u16(20),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(crc),
        u32(data.length),
        u32(data.length),
        u16(nameBytes.length),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(0),
        u32(offset),
        nameBytes,
      ]);
      localParts.push(local);
      centralParts.push(central);
      offset += local.length;
    }
    const centralDir = concat(centralParts);
    const end = concat([
      u32(0x06054b50),
      u16(0),
      u16(0),
      u16(entries.length),
      u16(entries.length),
      u32(centralDir.length),
      u32(offset),
      u16(0),
    ]);
    return concat([...localParts, centralDir, end]);
  }

  async function fetchBytes(url) {
    const res = await fetch(`${FETCH_PATH}?url=${encodeURIComponent(url)}`, {
      credentials: "same-origin",
      headers: { Accept: "*/*" },
    });
    if (res.status === 401) throw new Error("Not signed in.");
    const type = res.headers.get("content-type") || "";
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      if (type.includes("application/json")) {
        try {
          const payload = await res.json();
          detail = payload.message || detail;
        } catch {
          /* ignore */
        }
      }
      throw new Error(detail);
    }
    return new Uint8Array(await res.arrayBuffer());
  }

  function sameOrigin(base, candidate) {
    try {
      return new URL(base).origin === new URL(candidate, base).origin;
    } catch {
      return false;
    }
  }

  function safeLocalPath(assetUrl) {
    const u = new URL(assetUrl);
    let path = decodeURIComponent(u.pathname || "/").replace(/^\/+/, "");
    if (!path || path.endsWith("/")) path += "index.html";
    const parts = path
      .replace(/\\/g, "/")
      .split("/")
      .filter((p) => p && p !== "." && p !== "..");
    if (!parts.length) parts.push("asset");
    return ["assets", ...parts].join("/");
  }

  function collectAssetUrls(doc, pageUrl) {
    const urls = new Set();
    const attrs = [
      ["link[href]", "href"],
      ["script[src]", "src"],
      ["img[src]", "src"],
      ["source[src]", "src"],
      ["video[src]", "src"],
      ["audio[src]", "src"],
      ["image[href]", "href"],
      ["use[href]", "href"],
    ];
    for (const [sel, attr] of attrs) {
      doc.querySelectorAll(sel).forEach((el) => {
        const raw = el.getAttribute(attr);
        if (!raw || raw.startsWith("data:") || raw.startsWith("blob:") || raw.startsWith("javascript:")) return;
        try {
          const abs = new URL(raw, pageUrl).href;
          if (sameOrigin(pageUrl, abs)) urls.add(abs);
        } catch {
          /* skip */
        }
      });
    }
    return [...urls];
  }

  function rewriteHtml(html, pageUrl, map) {
    let out = html;
    const pairs = [...map.entries()].sort((a, b) => b[0].length - a[0].length);
    for (const [abs, local] of pairs) {
      const variants = new Set([abs, new URL(abs).pathname + new URL(abs).search]);
      try {
        variants.add(new URL(abs).href.replace(new URL(pageUrl).origin, ""));
      } catch {
        /* ignore */
      }
      for (const v of variants) {
        if (v) out = out.split(v).join(local);
      }
    }
    if (!/<base\s/i.test(out)) out = out.replace(/<head([^>]*)>/i, '<head$1><base href="./">');
    return out;
  }

  function slugName(url) {
    try {
      const u = new URL(url);
      const leaf = u.pathname.split("/").filter(Boolean).pop() || u.hostname;
      return leaf.replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "") || "site";
    } catch {
      return "site";
    }
  }

  async function pack() {
    const pageUrl = (els.url.value || "").trim();
    if (!pageUrl) {
      log("Enter a page URL.", "err");
      return;
    }
    let parsed;
    try {
      parsed = new URL(pageUrl);
    } catch {
      log("That URL is not valid.", "err");
      return;
    }
    if (!/^https?:$/i.test(parsed.protocol)) {
      log("Only http(s) URLs are supported.", "err");
      return;
    }

    els.go.disabled = true;
    els.log.innerHTML = "";
    log(`Fetching ${pageUrl} …`, "muted");

    try {
      const htmlBytes = await fetchBytes(pageUrl);
      const html = new TextDecoder("utf-8").decode(htmlBytes);
      log(`Got HTML (${html.length} chars).`, "ok");
      const doc = new DOMParser().parseFromString(html, "text/html");
      const assetUrls = collectAssetUrls(doc, pageUrl).slice(0, MAX_ASSETS);
      log(`Found ${assetUrls.length} same-site asset(s).`, "muted");

      const files = {};
      const urlMap = new Map();
      let total = 0;
      for (const abs of assetUrls) {
        const local = safeLocalPath(abs);
        if (files[local]) {
          urlMap.set(abs, local);
          continue;
        }
        try {
          log(`  + ${abs}`, "muted");
          const bytes = await fetchBytes(abs);
          total += bytes.length;
          if (total > MAX_BYTES) throw new Error("Packed size exceeded 40 MB limit.");
          files[local] = bytes;
          urlMap.set(abs, local);
        } catch (err) {
          log(`  skip ${abs} (${err.message})`, "err");
        }
      }

      files["index.html"] = new TextEncoder().encode(rewriteHtml(html, pageUrl, urlMap));
      log(`Building zip (${Object.keys(files).length} files)…`, "muted");
      const zipBytes = buildZip(files);
      const preferred = (els.name.value || "").trim() || `${slugName(pageUrl)}.zip`;
      const filename = preferred.toLowerCase().endsWith(".zip") ? preferred : `${preferred}.zip`;
      const blob = new Blob([zipBytes], { type: "application/zip" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      log(`Downloaded ${filename}. Switch to Add file and upload the zip as a Site.`, "ok");
    } catch (err) {
      log(String(err.message || err), "err");
    } finally {
      els.go.disabled = false;
    }
  }

  els.go.addEventListener("click", pack);
  els.url.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      pack();
    }
  });
})();
