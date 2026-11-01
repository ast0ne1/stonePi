"""Link previews (pinboard-canvas.md §5): title, site name, description, image and icon.

Fetched in the background when a link card is added, never on page load. The preview
image and icon are saved in ``<data>/previews/`` and served by Pinboard itself, so the
board never loads third-party images in the viewer's browser.

Safety: only http(s); every hop (the page, each redirect, the images) must resolve to
public addresses only, so a pasted link can't make the Pi probe the home network,
itself or the tailnet (100.64.0.0/10 is not global either), and only on ports 80 and
443. (httpx resolves the name again to connect; a host that changes its answer in
between could still slip through, which is accepted for a household board.) Short timeouts and size
caps; HTML only for the page, raster images only for the pictures (no SVG). A failed
fetch leaves a plain link card with its URL.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app import __github__, store

logger = logging.getLogger("pinboard.links")

TIMEOUT = httpx.Timeout(6.0, connect=4.0)
MAX_REDIRECTS = 4
PAGE_MAX = 768 * 1024
IMAGE_MAX = 2 * 1024 * 1024
ICON_MAX = 256 * 1024
# Sites such as Wikipedia refuse fetchers that give no contact link.
USER_AGENT = f"Mozilla/5.0 (compatible; StonePi-Pinboard/1.0; +{__github__})"
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}
MEDIA_TYPES = {ext: mime for mime, ext in IMAGE_TYPES.items()}


class Blocked(Exception):
    """The address isn't one Pinboard will fetch."""


def public_host(host: str) -> bool:
    """True when every address the host resolves to is a public (global unicast) one."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _check(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise Blocked(url)
    if parts.port not in (None, 80, 443):
        raise Blocked(url)
    if not public_host(parts.hostname):
        raise Blocked(url)


def _get(client: httpx.Client, url: str, cap: int, accept: str) -> tuple[str, str, bytes]:
    """(final url, content type, body up to ``cap``), following redirects by hand so each
    hop is checked."""
    for _ in range(MAX_REDIRECTS + 1):
        _check(url)
        with client.stream("GET", url, headers={"Accept": accept}) as resp:
            if resp.is_redirect and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            body = bytearray()
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) > cap:
                    if accept.startswith("text/html"):
                        break  # the head is all we need
                    raise ValueError("too large")
            return url, ctype, bytes(body)
    raise ValueError("too many redirects")


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.icons: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if a.get("href") and ("icon" in rel.split() or "apple-touch-icon" in rel):
                # Apple touch icons are bigger and sharper; try them first.
                self.icons.insert(0 if "apple" in rel else len(self.icons), a["href"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 300:
            self.title += data


def parse_page(html: str, base: str) -> dict[str, str | list[str]]:
    parser = _Meta()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - broken HTML still gives what was read
        pass
    m = parser.meta

    def first(*keys: str) -> str:
        return next((m[k] for k in keys if m.get(k)), "")

    image = first("og:image", "og:image:url", "twitter:image", "twitter:image:src")
    icons = [urljoin(base, href) for href in parser.icons] + [urljoin(base, "/favicon.ico")]
    return {
        "title": " ".join((first("og:title", "twitter:title") or parser.title).split())[: store.TITLE_MAX],
        "site": " ".join(first("og:site_name", "application-name").split())[:60],
        "description": " ".join(first("og:description", "description", "twitter:description").split())[:300],
        "image": urljoin(base, image) if image else "",
        "icons": icons,
    }


def _save_image(client: httpx.Client, url: str, card_id: str, kind: str, cap: int) -> str:
    """Download one picture into previews/; returns its file name or ''."""
    if not url:
        return ""
    try:
        _url, ctype, body = _get(client, url, cap, "image/*")
    except Exception:  # noqa: BLE001
        return ""
    ext = IMAGE_TYPES.get(ctype)
    if not ext or not body:
        return ""
    folder = store.previews_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{card_id}-{kind}.*"):
        old.unlink(missing_ok=True)
    name = f"{card_id}-{kind}.{ext}"
    (folder / name).write_bytes(body)
    return name


def fetch_preview(card_id: str) -> str:
    """Fetch and store one link card's preview; returns 'ok', 'failed' or 'blocked'."""
    card = store.get_card(card_id)
    if card is None or card["type"] != "link":
        return "failed"
    url = str(card["data"].get("url") or "")
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
            final, ctype, body = _get(client, url, PAGE_MAX, "text/html,application/xhtml+xml")
            if ctype not in {"text/html", "application/xhtml+xml"}:
                raise ValueError(f"not a page ({ctype})")
            page = parse_page(body.decode("utf-8", errors="replace"), final)
            image = _save_image(client, str(page["image"]), card_id, "image", IMAGE_MAX)
            icon = ""
            for candidate in page["icons"]:
                icon = _save_image(client, candidate, card_id, "icon", ICON_MAX)
                if icon:
                    break
    except Blocked:
        store.set_link_preview(card_id, {"preview": "blocked"})
        return "blocked"
    except Exception as exc:  # noqa: BLE001
        logger.info("Link preview for %s failed: %s", url, exc)
        store.set_link_preview(card_id, {"preview": "failed"})
        return "failed"
    found = {
        "title": str(page["title"]),
        "site": str(page["site"]),
        "description": str(page["description"]),
        "image": image,
        "icon": icon,
        "preview": "ok",
    }
    if not found["title"]:
        del found["title"]  # keep a title the share sheet gave us
    store.set_link_preview(card_id, found)
    return "ok"


def fetch_preview_later(card_id: str) -> None:
    threading.Thread(target=fetch_preview, args=(card_id,), daemon=True, name="pinboard-preview").start()
