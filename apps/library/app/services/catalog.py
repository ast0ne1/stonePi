"""Kiwix OPDS catalogue: fetch, cache, and resolve downloads.

The official catalogue stays the source of truth; StonePi only caches the
entries it shows (featured titles plus anything looked up by name), so the
Browse page keeps working through an internet outage.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app import __version__
from app.config import CATALOG_CACHE, FEATURED, FLAVOUR_LABELS, FLAVOUR_ORDER, env

logger = logging.getLogger("library.catalog")

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"
METALINK = "{urn:ietf:params:xml:ns:metalink}"
ACQUISITION = "http://opds-spec.org/acquisition/open-access"
CACHE_MAX_AGE_HOURS = 24

_lock = threading.Lock()
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,120}$", re.I)
# Settings use two-letter codes; the catalogue uses ISO 639-3.
_ISO3 = {"en": "eng", "da": "dan", "de": "deu", "es": "spa", "fr": "fra", "nl": "nld", "nb": "nob", "sv": "swe"}
_DATED_FILE_RE = re.compile(r"^(?P<stem>.+?)_(?P<date>\d{4}-\d{2})\.zim(?:\.meta4)?$")


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=env.request_timeout,
        follow_redirects=True,
        headers={"User-Agent": f"StonePi-Library/{__version__}"},
    )


def _text(node: ET.Element, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else ""


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        meta4 = ""
        size = 0
        for link in entry.findall(f"{ATOM}link"):
            if link.get("rel") == ACQUISITION:
                meta4 = link.get("href") or ""
                try:
                    size = int(link.get("length") or 0)
                except ValueError:
                    size = 0
        if not meta4:
            continue
        raw_id = _text(entry, f"{ATOM}id")
        issued = _text(entry, f"{DC}issued") or _text(entry, f"{ATOM}updated")
        out.append(
            {
                "book_id": raw_id.removeprefix("urn:uuid:"),
                "title": _text(entry, f"{ATOM}title"),
                "summary": _text(entry, f"{ATOM}summary"),
                "language": _text(entry, f"{ATOM}language"),
                "name": _text(entry, f"{ATOM}name"),
                "flavour": _text(entry, f"{ATOM}flavour"),
                "category": _text(entry, f"{ATOM}category"),
                "issued": issued[:10],
                "size": size,
                "meta4": meta4,
                "file_name": file_name_from_url(meta4),
            }
        )
    return out


SAFE_ZIM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.zim")


def file_name_from_url(url: str) -> str:
    path = urlsplit(url).path
    name = path.rsplit("/", 1)[-1]
    return name.removesuffix(".meta4")


def flavour_label(flavour: str) -> str:
    return FLAVOUR_LABELS.get(flavour or "", flavour.capitalize() if flavour else "Standard")


def _flavour_rank(flavour: str) -> int:
    try:
        return FLAVOUR_ORDER.index(flavour or "")
    except ValueError:
        return len(FLAVOUR_ORDER)


# ---------- cache ----------

def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fetched_at": "", "names": {}}


def _save_cache(cache: dict[str, Any]) -> None:
    CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CATALOG_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    tmp.replace(CATALOG_CACHE)


def fetched_at() -> str:
    return _load_cache().get("fetched_at", "")


def cache_is_stale() -> bool:
    stamp = fetched_at()
    if not stamp:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamp)
    except ValueError:
        return True
    return age.total_seconds() > CACHE_MAX_AGE_HOURS * 3600


def _fetch_name(client: httpx.Client, name: str) -> list[dict[str, Any]]:
    resp = client.get(f"{env.catalog_url.rstrip('/')}/catalog/v2/entries", params={"name": name, "count": 50})
    resp.raise_for_status()
    return parse_feed(resp.text)


def featured_names(lang: str) -> list[str]:
    return [f["name"].format(lang=lang) for f in FEATURED]


def refresh(lang: str, extra_names: list[str] | None = None) -> tuple[bool, str]:
    """Refetch featured titles (+ installed names). Keeps the old cache on failure."""
    names = list(dict.fromkeys(featured_names(lang) + list(extra_names or [])))
    with _lock:
        cache = _load_cache()
        try:
            with _client() as client:
                for name in names:
                    cache["names"][name] = _fetch_name(client, name)
        except (httpx.HTTPError, ET.ParseError) as exc:
            logger.warning("catalogue refresh failed: %s", exc)
            return False, "Couldn’t reach the Kiwix catalogue — showing the last saved copy."
        cache["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_cache(cache)
    return True, "Catalogue updated"


def entries_for(name: str) -> list[dict[str, Any]]:
    return list(_load_cache().get("names", {}).get(name, []))


def lookup(name: str) -> tuple[list[dict[str, Any]], str | None]:
    """Entries for a catalogue name, fetched live and cached."""
    name = name.strip()
    if not _NAME_RE.match(name):
        return [], "That doesn’t look like a Kiwix name"
    try:
        with _client() as client:
            entries = _fetch_name(client, name)
    except (httpx.HTTPError, ET.ParseError):
        cached = entries_for(name)
        if cached:
            return cached, None
        return [], "Couldn’t reach the Kiwix catalogue"
    with _lock:
        cache = _load_cache()
        cache.setdefault("names", {})[name] = entries
        _save_cache(cache)
    return entries, None


def _book_from_url(url: str) -> str:
    """The book or file name in a download link or a browse.library.kiwix.org link.

    browse.library.kiwix.org/viewer#wikipedia_en_100_2026-08/A/Page,
    …/content/wikipedia_en_100_2026-08/A/Page, …?name=wikipedia_en_100 (or
    books.name=…), and download.kiwix.org/…/file.zim all name the same book.
    """
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if "=" in parts.fragment:  # the browse page's own filters: #lang=eng&books.name=…
        query = {**parse_qs(parts.fragment), **query}
    for key in ("books.name", "name", "content"):
        if query.get(key):
            return query[key][0]
    if parts.fragment and parts.path.rstrip("/").endswith("/viewer"):
        return parts.fragment.lstrip("/").split("/", 1)[0]
    segments = [s for s in parts.path.split("/") if s]
    if "content" in segments[:-1]:
        return segments[segments.index("content") + 1]
    return segments[-1] if segments else ""


def looks_like_name(text: str) -> bool:
    """A Kiwix name, file name or link (looked up as-is) rather than search words."""
    raw = text.strip()
    return "://" in raw or raw.endswith((".zim", ".meta4")) or (" " not in raw and "_" in raw and bool(_NAME_RE.match(raw)))


def search(words: str, lang: str = "", limit: int = 12) -> tuple[list[dict[str, Any]], str | None]:
    """Titles in the whole Kiwix catalogue matching ``words``: [{name, title, summary, language, entries}].

    The entries are cached like a name lookup, so Install works on any result.
    """
    words = " ".join(words.split())[:100]
    if len(words) < 2:
        return [], "Type at least two letters"
    params: dict[str, Any] = {"q": words, "count": 80}
    try:
        with _client() as client:
            resp = client.get(f"{env.catalog_url.rstrip('/')}/catalog/v2/entries", params=params)
            resp.raise_for_status()
            entries = parse_feed(resp.text)
    except (httpx.HTTPError, ET.ParseError):
        return [], "Couldn’t reach the Kiwix catalogue"
    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        if not e["name"]:
            continue
        g = groups.setdefault(e["name"], {"name": e["name"], "title": e["title"], "summary": e["summary"], "language": e["language"], "entries": []})
        g["entries"].append(e)
    results = list(groups.values())
    # The household's language first, then the catalogue's own order.
    iso3 = _ISO3.get(lang, lang)
    if iso3:
        results.sort(key=lambda g: 0 if iso3 in (g["language"] or "").split(",") else 1)
    results = results[:limit]
    with _lock:
        cache = _load_cache()
        names = cache.setdefault("names", {})
        for g in results:
            known = {e["file_name"] for e in names.get(g["name"], [])}
            names[g["name"]] = names.get(g["name"], []) + [e for e in g["entries"] if e["file_name"] not in known]
        _save_cache(cache)
    return results, None


def parse_user_reference(text: str) -> tuple[str, str | None]:
    """Turn a pasted link or file name into (catalogue name, flavour or None).

    Accepts a catalogue name (``wikipedia_da_all``), a ZIM file name
    (``wikipedia_da_all_maxi_2026-05.zim``) or a download / browse link.
    """
    raw = text.strip()
    if "://" in raw:
        raw = _book_from_url(raw)
    raw = raw.strip()
    dated = _DATED_FILE_RE.match(raw if raw.endswith((".zim", ".meta4")) else f"{raw}.zim")
    if dated:
        stem = dated.group("stem")
        for flavour in ("maxi", "nopic", "mini"):
            if stem.endswith(f"_{flavour}"):
                return stem[: -len(flavour) - 1], flavour
        return stem, ""
    return raw, None


def variants(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest entry per flavour, most complete first."""
    best: dict[str, dict[str, Any]] = {}
    for e in entries:
        key = e.get("flavour") or ""
        if key not in best or e.get("issued", "") > best[key].get("issued", ""):
            best[key] = e
    rows = sorted(best.values(), key=lambda e: _flavour_rank(e.get("flavour") or ""))
    for row in rows:
        row["flavour_label"] = flavour_label(row.get("flavour") or "")
    return rows


def newest(name: str, flavour: str) -> dict[str, Any] | None:
    for e in variants(entries_for(name)):
        if (e.get("flavour") or "") == (flavour or ""):
            return e
    return None


# ---------- metalink ----------

def resolve_download(meta4_url: str) -> dict[str, Any]:
    """Download URL, exact size and SHA-256 from Kiwix's .meta4 file."""
    url = meta4_url.removesuffix(".meta4")
    with _client() as client:
        resp = client.get(meta4_url if meta4_url.endswith(".meta4") else f"{meta4_url}.meta4")
        resp.raise_for_status()
    root = ET.fromstring(resp.text)
    file_el = root.find(f"{METALINK}file")
    if file_el is None:
        raise ValueError("metalink has no file entry")
    size = int(_text(file_el, f"{METALINK}size") or 0)
    sha256 = ""
    for h in file_el.findall(f"{METALINK}hash"):
        if h.get("type") == "sha-256":
            sha256 = (h.text or "").strip().lower()
    name = file_el.get("name") or file_name_from_url(url)
    # The name becomes a path under the content folder: plain *.zim names only.
    if not SAFE_ZIM_NAME.fullmatch(name):
        raise ValueError("metalink has an unsafe file name")
    return {"url": url, "size": size, "sha256": sha256, "file_name": name}
