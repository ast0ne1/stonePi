from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STRIP_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
    }
)


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    query_tuples = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        if k.lower() not in STRIP_PARAMS:
            query_tuples.append((k, v))
    query_tuples.sort()
    query = urlencode(query_tuples)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_string(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def compute_event_fingerprint(
    user_id: int,
    title: str,
    start_time: datetime,
    location: str | None = None,
) -> str:
    norm_title = normalize_string(title)
    # Group by calendar date (YYYY-MM-DD)
    date_str = start_time.strftime("%Y-%m-%d")
    norm_loc = normalize_string(location or "")
    payload = f"{user_id}|{norm_title}|{date_str}|{norm_loc}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# -- cross-source matching (U33) ---------------------------------------------------
#
# The fingerprint above only catches exact repeats. These helpers decide whether
# two listings from *different* sources are the same event despite small
# differences ("Fran De Pian: Nostalgiología" at "Union" vs "Fran De Pian –
# Nostalgiologia" at "Union, Nørrebrogade 1"). They lean towards "different":
# showing a duplicate is better than merging two real events.

_FOLD = str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ß": "ss"})
_COMBINING = re.compile("[̀-ͯ]")
_TITLE_FILLER = frozenset(
    {
        "the", "a", "an", "and", "og", "i", "en", "et", "med", "at", "pa", "til", "of", "in", "for", "with",
        "tickets", "ticket", "billetter", "billet", "koncert", "concert", "presents", "praesenterer",
        "live", "feat", "ft", "vs", "x",
    }
)
# City and district words say nothing about which venue it is.
_PLACE_FILLER = frozenset(
    {
        "copenhagen", "kobenhavn", "kbh", "denmark", "danmark", "dk", "frederiksberg",
        "norrebro", "vesterbro", "osterbro", "amager", "valby", "vanlose", "bronshoj",
        "husum", "sydhavnen", "nordhavn", "christianshavn", "indre", "by",
        "n", "nv", "s", "sv", "k", "v", "o", "c",
    }
)
_UNKNOWN_PLACES = frozenset({"", "unspecified", "online", "tba", "tbd", "tbc", "various", "flere steder"})
# One distinctive word is enough for "Søndagsbrød" ⊂ "Søndagsbrød: Floor"; short ones ("Jazz") are not.
_MIN_SINGLE_TITLE_CHARS = 6

MAX_START_GAP = timedelta(minutes=90)


def fold(text: str | None) -> str:
    """Lowercase, accents and Danish letters folded, punctuation to spaces."""
    text = unicodedata.normalize("NFKD", (text or "").lower().translate(_FOLD))
    text = _COMBINING.sub("", text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def _tokens(text: str | None, filler: frozenset[str]) -> set[str]:
    return {t for t in fold(text).split() if t not in filler and not re.fullmatch(r"\d{4}", t)}


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a, _TITLE_FILLER), _tokens(b, _TITLE_FILLER)
    if not ta or not tb:
        return SequenceMatcher(None, fold(a), fold(b)).ratio()
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    # One title containing the other ("Fran De Pian" in "Fran De Pian: Nostalgiología",
    # or "Søndagsbrød" in "Søndagsbrød: Floor"). A single short word ("Jazz") is not enough.
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    contained = 0.0
    if shorter and shorter <= longer:
        if len(shorter) >= 2 or len(next(iter(shorter))) >= _MIN_SINGLE_TITLE_CHARS:
            contained = 1.0
    elif min(len(ta), len(tb)) >= 2:
        contained = len(ta & tb) / min(len(ta), len(tb))
    return max(seq, contained)


def venue_similarity(a: str | None, b: str | None) -> float | None:
    """None when either side doesn't say where it is."""
    if fold(a) in _UNKNOWN_PLACES or fold(b) in _UNKNOWN_PLACES:
        return None
    ta, tb = _tokens(a, _PLACE_FILLER), _tokens(b, _PLACE_FILLER)
    if not ta or not tb:
        return None
    return len(ta & tb) / min(len(ta), len(tb))


def has_time_of_day(dt: datetime) -> bool:
    return not (dt.hour == 0 and dt.minute == 0)


@dataclass
class Listing:
    title: str
    start_time: datetime
    location: str = ""
    url: str = ""


def match_reason(a: Listing, b: Listing) -> str | None:
    """Why `a` and `b` are the same event, or None when they aren't."""
    if a.start_time.date() != b.start_time.date():
        return None
    both_timed = has_time_of_day(a.start_time) and has_time_of_day(b.start_time)
    gap = abs(a.start_time - b.start_time)
    if both_timed and gap > MAX_START_GAP:
        return None
    if a.url and b.url and canonicalize_url(a.url) == canonicalize_url(b.url):
        return "same link"
    title = title_similarity(a.title, b.title)
    venue = venue_similarity(a.location, b.location)
    if venue is None:
        # Nothing to say where: only a specific title at the same time will do,
        # never a generic one ("Lokaludvalgsmøde" is on in every district).
        specific = min(len(_tokens(a.title, _TITLE_FILLER)), len(_tokens(b.title, _TITLE_FILLER))) >= 2
        if title >= 0.9 and specific and (not both_timed or gap <= timedelta(minutes=15)):
            return "same title, place not given"
        return None
    if title >= 0.9 and venue >= 0.5:
        return "same title and place"
    if title >= 0.75 and venue >= 0.7 and (not both_timed or gap <= timedelta(minutes=30)):
        return "similar title, same place"
    return None
