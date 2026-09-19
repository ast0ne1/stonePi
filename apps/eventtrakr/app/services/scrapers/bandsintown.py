from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

# Like Songkick, Bandsintown's listing page already carries a full array of
# schema.org MusicEvent JSON-LD, so SchemaOrgExtractor handles extraction
# directly -- this module only pins the "date" range filter Bandsintown's
# own UI uses (a single comma-separated "start,end" ISO pair) to
# today 00:00..(today+days) 23:00.


def is_listing_url(url: str) -> bool:
    return "/c/" in urlsplit(url).path


def with_date_window(url: str, days: int) -> str:
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    end = end.replace(hour=23)
    query["date"] = [f"{start.strftime('%Y-%m-%dT%H:%M:%S')},{end.strftime('%Y-%m-%dT%H:%M:%S')}"]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
