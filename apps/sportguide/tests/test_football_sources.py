"""Football: timezone.football (local channels) merged with WheresTheMatch (UK)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.collectors.wheresthematch import merge, parse_timezone_football, parse_wtm
from app.timeutil import normalize_football_league

NOW = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)

TZ_HTML = """
<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
 {"@type":"ListItem","item":{"@type":"SportsEvent","startDate":"2026-10-03T16:00:00.000:00Z",
  "url":"https://timezone.football/nations-league/match/2026-10-03-croatia-v-england/"}},
 {"@type":"ListItem","item":{"@type":"SportsEvent","startDate":"2026-10-03T13:00:00.000:00Z",
  "url":"https://timezone.football/nations-league/match/2026-10-03-finland-v-albania/"}},
 {"@type":"ListItem","item":{"@type":"SportsEvent","startDate":"2026-10-03T11:30:00:00Z","url":"https://timezone.football/friendlies/"}},
 {"@type":"ListItem","item":{"@type":"SportsEvent","startDate":"2026-10-03T08:00:00:00Z","url":"https://timezone.football/friendlies/"}}
]}</script>
<h2 class="wk-h">Saturday 3 October <span>4 matches</span></h2>
<ul class="tm-list">
 <li class="tm-row"><div class="tm-status"><a class="tm-status-link" href="/friendlies/"><span class="tm-hh">12:30</span></a></div>
  <div class="tm-mid"><div class="tm-team"><a class="tm-name">Crystal Palace</a></div><div class="tm-team"><a class="tm-name">Bristol City</a></div>
  <div class="tm-meta"><img class="tm-meta-comp" alt="Friendlies"/></div></div>
  <span class="tm-ch"><img class="flag tm-ch-flag" alt="Denmark"/><span class="tm-chip tm-ch-club"><span class="tm-chip-name">Club's official stream</span></span></span></li>
 <li class="tm-row"><div class="tm-status"><a class="tm-status-link" href="/nations-league/match/2026-10-03-finland-v-albania/"><span class="tm-hh">15:00</span></a></div>
  <div class="tm-mid"><div class="tm-team"><a class="tm-name">Finland</a></div><div class="tm-team"><a class="tm-name">Albania</a></div>
  <div class="tm-meta"><img class="tm-meta-comp" alt="Nations League"/></div></div>
  <span class="tm-ch"><img class="flag tm-ch-flag" alt="Denmark"/><span class="tm-chip tm-ch-alt"><span class="tm-chip-name">DAZN</span><a class="tm-vpn">VPN</a></span></span></li>
 <li class="tm-row"><div class="tm-status"><a class="tm-status-link" href="/nations-league/match/2026-10-03-croatia-v-england/"><span class="tm-hh">18:00</span></a></div>
  <div class="tm-mid"><div class="tm-team"><a class="tm-name">Croatia</a></div><div class="tm-team"><a class="tm-name">England</a></div>
  <div class="tm-meta"><img class="tm-meta-comp" alt="Nations League"/></div></div>
  <span class="tm-ch"><img class="flag tm-ch-flag" alt="Denmark"/><span class="tm-chip"><span class="tm-chip-name">TV 2 Sport</span></span></span></li>
 <li class="tm-row"><div class="tm-status"><a class="tm-status-link" href="/bundesliga/match/2026-10-03-st-pauli-v-gladbach/"><span class="tm-hh">16:30</span></a></div>
  <div class="tm-mid"><div class="tm-team"><a class="tm-name">St Pauli</a></div><div class="tm-team"><a class="tm-name">Borussia Mönchengladbach</a></div>
  <div class="tm-meta"><img class="tm-meta-comp" alt="International Friendlies"/></div></div>
  <span class="tm-ch"><img class="flag tm-ch-flag" alt="Denmark"/><span class="tm-chip tm-ch-season"><span class="tm-chip-name">Viaplay rights holder</span></span></span></li>
</ul>
"""

WTM_HTML = """
<table>
<tr>
 <td class="home-team"><a href="https://www.wheresthematch.com/match/croatia-vs-england/1"></a></td>
 <td class="fixture-details"><span class="fixture"><a><em>Croatia</em></a><em>v</em><a><em>England</em></a></span></td>
 <td class="start-details"><time class="sr-only" datetime="2026-10-03T17:00:00+01:00"></time></td>
 <td class="competition-name"><span>UEFA Nations League</span></td>
 <td class="channel-details"><a href="https://www.wheresthematch.com/match/croatia-vs-england/1">
  <img class="channel" alt="Croatia v England Broadcast on ITV1"/><span class="sr-only">ITV1</span></a>
  <a><span class="sr-only">ITVX</span></a></td>
</tr>
<tr>
 <td class="fixture-details"><span class="fixture"><a><em>Arsenal</em></a><em>v</em><a><em>Chelsea</em></a></span></td>
 <td class="start-details"><time datetime="2026-10-04T16:30:00+01:00"></time></td>
 <td class="competition-name"><span>Premier League</span></td>
 <td class="channel-details"><a href="https://www.wheresthematch.com/match/arsenal-vs-chelsea/2"><img class="channel" alt="Arsenal v Chelsea Broadcast on Sky Sports Main Event"/></a></td>
</tr>
<tr><td>Navigation row without a fixture</td></tr>
</table>
"""


def _by_title(rows):
    return {r.title: r for r in rows}


def test_timezone_football_rows_country_and_times():
    rows, country = parse_timezone_football(TZ_HTML, now=NOW)
    assert country == "Denmark"
    by = {f"{r['home']} vs {r['away']}": r for r in rows}
    # Own match page: exact UTC kick-off from the page data.
    assert by["Croatia vs England"]["starts"] == datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert by["Croatia vs England"]["channels"] == ["TV 2 Sport"]
    # Friendlies share one link: the page clock (UTC+2 here) learnt from the others.
    assert by["Crystal Palace vs Bristol City"]["starts"] == datetime(2026, 10, 3, 10, 30, tzinfo=timezone.utc)
    assert by["Crystal Palace vs Bristol City"]["channels"] == ["Club's official stream"]
    # Only on a foreign service (VPN): not on TV here.
    assert by["Finland vs Albania"]["channels"] == [] and by["Finland vs Albania"]["status"] == "Not on TV in Denmark"
    assert by["St Pauli vs Borussia Mönchengladbach"]["channels"] == ["Viaplay (channel TBC)"]


def test_wheresthematch_rows():
    rows = parse_wtm(WTM_HTML)
    assert len(rows) == 2
    croatia = rows[0]
    assert croatia["starts"] == datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert croatia["channels"] == ["ITV1", "ITVX"]
    assert rows[1]["channels"] == ["Sky Sports Main Event"]
    assert croatia["url"].endswith("/croatia-vs-england/1")


def test_merge_combines_both_guides_per_match():
    tz_rows, country = parse_timezone_football(TZ_HTML, now=NOW)
    rows = _by_title(merge(tz_rows, parse_wtm(WTM_HTML), country))
    assert len(rows) == 5  # 4 from timezone.football + Arsenal v Chelsea from WheresTheMatch only
    croatia = rows["Croatia vs England"]
    assert croatia.channels == ["TV 2 Sport", "ITV1 (UK)", "ITVX (UK)"]
    assert croatia.source_url.startswith("https://timezone.football/nations-league/match/")
    assert rows["Finland vs Albania"].channels == ["Not on TV in Denmark"]
    assert rows["Arsenal vs Chelsea"].channels == ["Sky Sports Main Event (UK)"]
    assert rows["Arsenal vs Chelsea"].league == "Premier League"
    # Competition names decide the league, never club names.
    assert rows["Crystal Palace vs Bristol City"].league == "Other"
    assert rows["St Pauli vs Borussia Mönchengladbach"].league == "Other"


def test_uk_households_get_plain_uk_channels():
    tz_rows, _ = parse_timezone_football(TZ_HTML.replace("Denmark", "United Kingdom"), now=NOW)
    rows = _by_title(merge(tz_rows, parse_wtm(WTM_HTML), "United Kingdom"))
    assert rows["Croatia vs England"].channels == ["TV 2 Sport", "ITV1", "ITVX"]


def test_strict_league_ignores_club_names():
    assert normalize_football_league("International Friendlies", club_hints=False) == "Other"
    assert normalize_football_league("UEFA Champions League", club_hints=False) == "Champions League"
    assert normalize_football_league("Man United v Spennymoor") == "Premier League"  # old behaviour kept by default


def test_week_spanning_new_year_keeps_the_right_year():
    html = TZ_HTML.replace("Saturday 3 October", "Wednesday 31 December")
    rows, _ = parse_timezone_football(html, now=datetime(2027, 1, 2, tzinfo=timezone.utc))
    assert all(r["starts"].year == 2026 for r in rows)
