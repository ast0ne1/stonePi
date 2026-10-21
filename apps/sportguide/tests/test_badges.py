"""U28: team badges on the football cards."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.collectors.wheresthematch import merge, parse_timezone_football, parse_wtm
from app.services import badges
from app.timeutil import enrich_listing_row

NOW = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
CREST_A = "https://r2.thesportsdb.com/images/media/team/badge/aaa111.png"
CREST_B = "https://crests.football-data.org/57.png"

TZ_HTML = f"""
<h2 class="wk-h">Saturday 3 October</h2>
<li class="tm-row"><span class="tm-hh">17:00</span>
  <div class="tm-mid">
    <div class="tm-team"><img class="tm-crest" src="{CREST_A}" alt=""/><a class="tm-name">Lille</a></div>
    <div class="tm-team"><img class="tm-crest" src="{CREST_B}" alt=""/><a class="tm-name">Arsenal</a></div>
    <div class="tm-meta"><img class="tm-meta-comp" alt="Champions League"/></div></div>
  <span class="tm-ch"><img class="flag tm-ch-flag" alt="Denmark"/><span class="tm-chip"><span class="tm-chip-name">Viaplay</span></span></span></li>
"""

WTM_HTML = """
<table><tr>
 <td class="fixture-details"><span class="fixture">
  <a href="https://www.wheresthematch.com/Football/Japan.asp"><em>Japan</em></a><em>v</em>
  <a href="https://www.wheresthematch.com/Football/Brazil.asp"><em>Brazil</em></a></span></td>
 <td class="start-details"><time datetime="2026-10-03T12:00:00+01:00"></time></td>
 <td class="channel-details"><span class="sr-only">ITV1</span></td>
</tr></table>
"""


def test_badges_are_read_from_both_sources_and_merged():
    tz_rows, country = parse_timezone_football(TZ_HTML, now=NOW)
    assert tz_rows[0]["home_badge"] == CREST_A and tz_rows[0]["away_badge"] == CREST_B
    wtm_rows = parse_wtm(WTM_HTML)
    assert wtm_rows[0]["home_badge"] == "https://www.wheresthematch.com/images/clubcrests/Japan-1.gif"
    rows = {r.title: r for r in merge(tz_rows, wtm_rows, country)}
    lille = rows["Lille vs Arsenal"]
    assert (lille.home, lille.away, lille.home_badge, lille.away_badge) == ("Lille", "Arsenal", CREST_A, CREST_B)
    assert rows["Japan vs Brazil"].away_badge.endswith("/clubcrests/Brazil-1.gif")


def test_badges_download_once_from_allowed_hosts_and_misses_are_remembered(tmp_path, monkeypatch):
    monkeypatch.setattr(badges, "BADGE_DIR", tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("aaa111.png"):
            return httpx.Response(200, content=b"\x89PNG fake", headers={"content-type": "image/png"})
        if request.url.path.endswith(".svg"):
            return httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})
        return httpx.Response(404, text="nope", headers={"content-type": "text/html"})

    real_client = httpx.Client
    monkeypatch.setattr(badges.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    missing = "https://www.wheresthematch.com/images/clubcrests/Arsenal-1.gif"
    svg = "https://crests.football-data.org/57.svg"
    assert badges.fetch_missing([CREST_A, missing, svg, "https://evil.example/x.png", "http://r2.thesportsdb.com/a.png"]) == 1
    assert sorted(calls) == sorted([CREST_A, missing, svg])  # other hosts and plain http never fetched
    assert badges.local_src(svg) is None  # an SVG is never kept, even when the host serves one
    assert badges.local_src(CREST_A).startswith("/badges/") and badges.local_src(CREST_A).endswith(".png")
    assert badges.local_src(missing) is None
    calls.clear()
    assert badges.fetch_missing([CREST_A, missing]) == 0 and calls == []  # cached, and the miss is remembered


def test_row_gets_teams_with_badge_or_initials(tmp_path, monkeypatch):
    monkeypatch.setattr(badges, "BADGE_DIR", tmp_path)
    (tmp_path / (badges._key(CREST_A) + ".png")).write_bytes(b"png")
    row = enrich_listing_row(
        {"title": "Manchester United vs Arsenal", "starts_at": "2026-10-03T16:00:00Z", "channels": [],
         "home": "Manchester United", "away": "Arsenal", "home_badge": "", "away_badge": CREST_A},
        "Europe/Copenhagen", now=NOW,
    )
    assert row["teams"][0] == {"name": "Manchester United", "badge": None, "initials": "MU"}
    assert row["teams"][1]["badge"].startswith("/badges/") and row["teams"][1]["initials"] == "AR"
    assert enrich_listing_row({"title": "Rugby thing", "starts_at": "2026-10-03T16:00:00Z"}, "UTC", now=NOW)["teams"] == []
