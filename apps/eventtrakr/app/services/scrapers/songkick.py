from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

# Songkick's own listing pages already carry full schema.org MusicEvent
# JSON-LD per card, so SchemaOrgExtractor handles extraction directly --
# this module only pins the date-range filter Songkick's own UI uses
# (filters[minDate]/filters[maxDate], as MM/DD/YYYY) to today..today+days.


def is_listing_url(url: str) -> bool:
    return "/metro-areas/" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    today = date.today()
    end = today + timedelta(days=days)
    query["filters[minDate]"] = [today.strftime("%m/%d/%Y")]
    query["filters[maxDate]"] = [end.strftime("%m/%d/%Y")]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
