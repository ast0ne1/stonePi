import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from app.services.scrapers.schema_org import SchemaOrgExtractor
from app.services.scrapers.ics_feed import IcsFeedExtractor
from app.services.scrapers.eventbrite import EventbriteExtractor
from app.services.scrapers.madbillet import MadbilletExtractor
from app.services.scrapers.kultunaut import KultunautExtractor, with_date_window as with_kultunaut_date_window
from app.services.scrapers.migogkbh import MigogKbhExtractor
from app.services.scrapers.cphpost import CphPostExtractor, with_date_window as with_cphpost_date_window
from app.services.scrapers.residentadvisor import ResidentAdvisorExtractor, with_date_window as with_ra_date_window
from app.services.scrapers.songkick import with_date_window as with_songkick_date_window
from app.services.scrapers.bandsintown import with_date_window as with_bandsintown_date_window
from app.services.scrapers.brugbyen import BrugbyenExtractor, with_date_window as with_brugbyen_date_window
from app.services.scrapers import brightdata_facebook

SAMPLE_JSONLD = """
<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "Indie Rock Showcase",
  "startDate": "2026-09-22T20:00:00Z",
  "endDate": "2026-09-22T23:00:00Z",
  "location": {
    "@type": "Place",
    "name": "The Roundhouse",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "Chalk Farm Rd",
      "addressLocality": "London"
    }
  },
  "offers": {
    "@type": "Offer",
    "price": "15.00",
    "priceCurrency": "GBP"
  },
  "description": "An exciting evening of local indie rock bands."
}
</script>
</head>
<body></body>
</html>
"""

SAMPLE_ITEMLIST_JSONLD = """
<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "item": {
        "@type": "Event",
        "name": "Feminist Placemaking Academy",
        "startDate": "2026-09-23T09:00:00Z",
        "location": {
          "@type": "Place",
          "name": "Demokrati Garage",
          "address": {
            "@type": "PostalAddress",
            "streetAddress": "57 Rentemestervej",
            "addressLocality": "København"
          }
        }
      }
    }
  ]
}
</script>
</head>
<body></body>
</html>
"""

SAMPLE_EVENTBRITE_CARD = """
<!DOCTYPE html>
<html>
<body>
<section class="event-card-details">
  <div class="Stack_root__1ksk7">
    <a class="event-card-link" href="/e/test-concert-night-tickets-123">
      <h3 class="Typography_root__4atzy event-card__clamp-line--two">Test Concert Night</h3>
    </a>
    <p class="Typography_root__4atzy Typography_body-md-bold__4atzy">Sat 20 Sep, 20:00</p>
    <p class="Typography_root__4atzy event-card__clamp-line--one">Copenhagen &middot; Test Venue</p>
    <div class="DiscoverVerticalEventCard-module__priceWrapper___usWo6">
      <p class="Typography_root__4atzy">From 150,00&nbsp;kr</p>
    </div>
  </div>
</section>
</body>
</html>
"""

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:test-event-123@example.com
DTSTAMP:20260917T120000Z
DTSTART:20260920T180000Z
DTEND:20260920T210000Z
SUMMARY:London Python Dev Meetup
DESCRIPTION:Talks on Web scraping and async Python.
LOCATION:Skills Matter, London
CATEGORIES:Tech
END:VEVENT
END:VCALENDAR
"""


def test_schema_org_extractor():
    extractor = SchemaOrgExtractor()
    events = extractor.extract(SAMPLE_JSONLD, "https://example.com/events/1")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Indie Rock Showcase"
    assert "GBP 15.00" in ev.cost
    assert "Roundhouse" in ev.location
    assert ev.start_time == datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)


SAMPLE_AGGREGATE_OFFER_JSONLD = """
<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "EducationEvent",
  "name": "An Evening with Eckhart Tolle",
  "startDate": "2026-10-08T19:00:00Z",
  "location": {"@type": "Place", "name": "Bella Center Copenhagen"},
  "offers": [
    {
      "@type": "AggregateOffer",
      "lowPrice": "277.0",
      "highPrice": "1100.0",
      "priceCurrency": "DKK"
    }
  ]
}
</script>
</head>
<body></body>
</html>
"""


def test_schema_org_extractor_aggregate_offer():
    extractor = SchemaOrgExtractor()
    events = extractor.extract(SAMPLE_AGGREGATE_OFFER_JSONLD, "https://www.eventbrite.dk/e/test")
    assert len(events) == 1
    assert "277" in events[0].cost
    assert "DKK" in events[0].cost


def test_schema_org_extractor_itemlist():
    extractor = SchemaOrgExtractor()
    events = extractor.extract(SAMPLE_ITEMLIST_JSONLD, "https://www.eventbrite.dk/d/denmark--copenhagen/all-events/")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Feminist Placemaking Academy"
    assert "Demokrati Garage" in ev.location
    assert ev.start_time == datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def test_eventbrite_extractor():
    extractor = EventbriteExtractor()
    events = extractor.extract(SAMPLE_EVENTBRITE_CARD, "https://www.eventbrite.dk/d/denmark--copenhagen/all-events/")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Test Concert Night"
    assert "Test Venue" in ev.location
    assert "150" in ev.cost
    assert ev.start_time is not None


SAMPLE_MADBILLET_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="event-item">
  <div class="event-details">
    <a href="/show/event/single-day-tasting/0/0/0/english">
      <div class="event-pre-title">Some Restaurant presents</div>
      <div class="event-title">SINGLE DAY TASTING</div>
      <div class="event-price">195 DKK</div>
      <div class="event-date">20. sep</div>
    </a>
  </div>
</div>
<div class="event-item">
  <div class="event-details">
    <a href="/show/event/running-campaign/0/0/0/english">
      <div class="event-pre-title">A Charity presents</div>
      <div class="event-title">RUNNING CAMPAIGN</div>
      <div class="event-price">125-500 DKK</div>
      <div class="event-date">1. maj - 31. dec</div>
    </a>
  </div>
</div>
</body>
</html>
"""


def test_madbillet_extractor_single_date():
    events = MadbilletExtractor().extract(SAMPLE_MADBILLET_HTML, "https://madbillet.dk/show/index/english")
    assert len(events) == 2
    single = events[0]
    assert single.title == "SINGLE DAY TASTING"
    assert single.start_time.month == 9 and single.start_time.day == 20
    assert single.end_time is None
    assert "195" in single.cost


def test_madbillet_extractor_date_range():
    events = MadbilletExtractor().extract(SAMPLE_MADBILLET_HTML, "https://madbillet.dk/show/index/english")
    campaign = events[1]
    assert campaign.title == "RUNNING CAMPAIGN"
    assert campaign.start_time is not None
    assert campaign.end_time.month == 12 and campaign.end_time.day == 31


SAMPLE_KULTUNAUT_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="products arrlist" id="product-row">
  <div data-arrnr="20338803" class="product col-lg-6 col-md-6 col-sm-6 col-xs-6">
    <div style="position:relative">
      <a href="https://www.kultunaut.dk/perl/arrmore/type-nynaut?ArrNr=20338803" class="product-content" data-position="1" data-id="30801" data-price="125">
        <div class="kult-image"><img alt="" src="https://example.com/image1.jpg"></div>
        <div class="arr-info">
          <div class="arr-genre">
            <span class="genre_cat notranslate">Gastronomi</span>
            <h3><strong>Anarkist Beer Walk x Sensommer i Koebenhavn</strong></h3>
          </div>
          <div class="arr-description"><span>A walking beer tour around town.</span></div>
          <div class="kult-month-day">
            <time>Fre. 18. sep. 2026, Enghave Plads Metro</time>
          </div>
        </div>
      </a>
    </div>
  </div>
  <div data-arrnr="20338900" class="product col-lg-6 col-md-6 col-sm-6 col-xs-6">
    <div style="position:relative">
      <a href="https://www.kultunaut.dk/perl/arrmore/type-nynaut?ArrNr=20338900" class="product-content" data-position="2" data-id="30900" data-price="0">
        <div class="arr-info">
          <div class="arr-genre">
            <span class="genre_cat notranslate">Folk/Vise/Country</span>
            <h3><strong>Visens Venner Broenshoej</strong></h3>
          </div>
          <div class="arr-description"><span>A singalong evening.</span></div>
          <div class="kult-month-day">
            <time>Fre. 18. sep. 2026 19:00 - mulighed for faellesspisning kl. 18., Kulturhuset Broek</time>
          </div>
        </div>
      </a>
    </div>
  </div>
</div>
</body>
</html>
"""


def test_kultunaut_extractor():
    events = KultunautExtractor().extract(SAMPLE_KULTUNAUT_HTML, "https://www.kultunaut.dk/perl/arrlist/type-nynaut")
    assert len(events) == 2

    first = events[0]
    assert first.title == "Anarkist Beer Walk x Sensommer i Koebenhavn"
    assert first.location == "Enghave Plads Metro"
    assert first.start_time.month == 9 and first.start_time.day == 18
    assert first.cost == "Free / Unspecified"
    assert first.category == "Gastronomi"

    free = events[1]
    assert free.title == "Visens Venner Broenshoej"
    assert free.location == "Kulturhuset Broek"
    assert free.start_time.hour == 19
    assert free.cost == "Free / Unspecified"


def test_kultunaut_with_date_window():
    url = with_kultunaut_date_window(
        "https://www.kultunaut.dk/perl/arrlist/type-nynaut?showmap=&Area=Kbh.+og+Frederiksberg&nearmeradius=2000&Genre=",
        7,
    )
    assert "ArrStartdato=" in url
    assert "ArrSlutdato=" in url
    assert "Area=Kbh" in url


SAMPLE_MIGOGKBH_HTML = """
<!DOCTYPE html>
<html>
<body>
<article class="tpo:article-thumb is-guide is-layout-naked-image-card" data-article-id="200133">
    <div class="tpo:article-thumb--picture-container">
        <time class="tpo:calendar-badge" datetime="2026-09-19 10:00:00">
            <span class="tpo:calendar-badge--month">sep</span>
            <span class="tpo:calendar-badge--day">19</span>
        </time>
    </div>
    <div class="tpo:article-thumb--description">
        <h3 class="tpo:article-thumb--headline">FossilFestival</h3>
        <div class="tpo:article-thumb--excerpt">
            <time class="has-16-rem-font-size" datetime="2026-09-19 10:00:00">I morgen &middot; kl. 10:00 - 17:00</time>
            <br>
            <a href="https://migogkbh.dk/kalender/sted/botanisk-have/" rel="category tag" class="tpo:naked-decoration-link">Botanisk Have</a>
        </div>
        <div class="tpo:button-list is-medium-gab">
            <a href="https://snm.dk/da/arrangement/fossilfestival" class="tpo:label is-button"><span>Billet</span></a>
            <a href="https://migogkbh.dk/kalender/begivenhed/fossilfestival-2/" class="tpo:label is-button"><span>Laes mere</span></a>
        </div>
    </div>
</article>
</body>
</html>
"""


def test_migogkbh_extractor():
    events = MigogKbhExtractor().extract(SAMPLE_MIGOGKBH_HTML, "https://migogkbh.dk/kalender/")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "FossilFestival"
    assert ev.location == "Botanisk Have"
    assert ev.start_time == datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
    assert ev.url == "https://migogkbh.dk/kalender/begivenhed/fossilfestival-2/"


SAMPLE_CPHPOST_HTML = """
<!DOCTYPE html>
<html>
<body>
<div id="nautmasonry">
  <div class="nautmasonryitem genre-6">
    <div data-arrnr="20338803" class="squere">
      <div class="event-caption">
        <div class="eventtitle"><span class="notranslate">Anarchist Beer Walk x Late Summer in Copenhagen</span></div>
        <div class="date_time notranslate">
          <span>Fri 18 Sep 2026</span>
          <span class="tidspunkt_ev"></span>
        </div>
        <div class="eventplace notranslate">Enghave Plads Metro, Copenhagen V</div>
      </div>
      <div class="eventbody viewsmallscreen">
        <div class="info_bottom_body notranslate">
          <div class="button_rm_ticket">
            <a href="https://cphpost.dk/calendar/?tmplid=arrmore&ArrNr=20338803" class="readmore">Read more</a>
            <a class="eventticket" href="https://www.kultunaut.dk/perl/billet/type-cphpost/UK?ArrNr=20338803" target="_blank">Buy ticket</a>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="nautmasonryitem genre-3">
    <div data-arrnr="20338900" class="squere">
      <div class="event-caption">
        <div class="eventtitle"><span class="notranslate">Zwish Early Bird - Oesterbro</span></div>
        <div class="date_time notranslate">
          <span>Fri 18 Sep 2026</span>
          <span class="tidspunkt_ev">06:45 - 7.45 am</span>
        </div>
        <div class="eventplace notranslate">Oesterbrohuset, Copenhagen Oe</div>
      </div>
      <div class="eventbody viewsmallscreen">
        <div class="info_bottom_body notranslate">
          <div class="button_rm_ticket">
            <a href="https://cphpost.dk/calendar/?tmplid=arrmore&ArrNr=20338900" class="readmore">Read more</a>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""


def test_cphpost_extractor():
    events = CphPostExtractor().extract(SAMPLE_CPHPOST_HTML, "https://cphpost.dk/calendar/")
    assert len(events) == 2

    first = events[0]
    assert first.title == "Anarchist Beer Walk x Late Summer in Copenhagen"
    assert first.location == "Enghave Plads Metro, Copenhagen V"
    assert first.start_time == datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    assert "ArrNr=20338803" in first.url

    second = events[1]
    assert second.title == "Zwish Early Bird - Oesterbro"
    assert second.start_time == datetime(2026, 9, 18, 6, 45, tzinfo=timezone.utc)


def test_cphpost_with_date_window():
    url = with_cphpost_date_window(
        "https://cphpost.dk/calendar/?ArrKunstner=&Area=Kbh.+og+Frederiksberg&Rating=1", 7
    )
    assert "ArrStartdato=" in url
    assert "ArrSlutdato=" in url
    assert "Area=Kbh" in url


SAMPLE_RA_NEXT_DATA = {
    "props": {
        "apolloState": {
            "Venue:27969": {"id": "27969", "__typename": "Venue", "name": "Pumpehuset"},
            "Event:2464279": {
                "id": "2464279",
                "__typename": "Event",
                "title": "ESCAPISM presents: MPH [uk] 360° XP + Guests",
                "date": "2026-09-19T00:00:00.000",
                "contentUrl": "/events/2464279",
                "venue": {"__ref": "Venue:27969"},
                "startTime": "2026-09-19T21:00:00.000",
                "endTime": "2026-09-20T03:30:00.000",
            },
            "ROOT_QUERY": {"__typename": "Query"},
        }
    }
}

SAMPLE_RA_HTML = f"""
<!DOCTYPE html>
<html>
<body>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(SAMPLE_RA_NEXT_DATA)}</script>
</body>
</html>
"""


def test_resident_advisor_extractor():
    events = ResidentAdvisorExtractor().extract(SAMPLE_RA_HTML, "https://ra.co/events/dk/copenhagen")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "ESCAPISM presents: MPH [uk] 360° XP + Guests"
    assert ev.location == "Pumpehuset"
    assert ev.start_time == datetime(2026, 9, 19, 21, 0, tzinfo=timezone.utc)
    assert ev.end_time == datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    assert ev.url == "https://ra.co/events/2464279"


def test_resident_advisor_with_date_window():
    url = with_ra_date_window("https://ra.co/events/dk/copenhagen", 7)
    assert "startDate=" in url
    assert "endDate=" in url


SAMPLE_SONGKICK_HTML = """
<!DOCTYPE html>
<html>
<body>
<ul class="metro-area-calendar-listings">
  <li class="event-listings-element">
    <div class="microformat">
      <script type="application/ld+json">[{"@context":"http://schema.org","@type":"MusicEvent","name":"A$AP Rocky @ Royal Arena","url":"https://www.songkick.com/concerts/43010983-asap-rocky-at-royal-arena","location":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Copenhagen","addressCountry":"Denmark","streetAddress":"Hannemanns Alle 18","postalCode":"2300"},"name":"Royal Arena"},"startDate":"2026-09-18T19:30:00","endDate":"2026-09-18","performer":[{"@type":"MusicGroup","name":"A$AP Rocky"}],"offers":[{"@type":"Offer","url":"https://www.songkick.com/concerts/43010983-asap-rocky-at-royal-arena"}]}]</script>
    </div>
  </li>
  <li class="event-listings-element">
    <div class="microformat">
      <script type="application/ld+json">[{"@context":"http://schema.org","@type":"MusicEvent","name":"Alison Moyet @ Copenhagen Opera House","url":"https://www.songkick.com/concerts/43090764-alison-moyet","location":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Copenhagen","addressCountry":"Denmark"},"name":"Copenhagen Opera House"},"startDate":"2026-09-19T20:00:00","performer":[{"@type":"MusicGroup","name":"Alison Moyet"}]}]</script>
    </div>
  </li>
</ul>
</body>
</html>
"""


def test_songkick_uses_schema_org_extractor():
    events = SchemaOrgExtractor().extract(SAMPLE_SONGKICK_HTML, "https://www.songkick.com/metro-areas/28617-denmark-copenhagen")
    assert len(events) == 2
    assert events[0].title == "A$AP Rocky @ Royal Arena"
    assert events[0].start_time == datetime(2026, 9, 18, 19, 30, tzinfo=timezone.utc)
    assert "Royal Arena" in events[0].location
    assert events[1].title == "Alison Moyet @ Copenhagen Opera House"


def test_songkick_with_date_window():
    url = with_songkick_date_window("https://www.songkick.com/metro-areas/28617-denmark-copenhagen", 7)
    assert "filters%5BminDate%5D=" in url
    assert "filters%5BmaxDate%5D=" in url


def test_bandsintown_with_date_window():
    url = with_bandsintown_date_window("https://www.bandsintown.com/c/copenhagen-denmark/choose-dates/genre/all-genres", 7)
    assert "date=" in url
    assert "%2C" in url  # comma between start,end


SAMPLE_BRUGBYEN_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="node node--type-event node--promoted card-grid-item">
  <a href="/en/whats-on/copenhagen-cottage-market">
    <div class="node__content">
      <div class="pre-title">Hoejbro Plads</div>
      <div class="title"><h3 class="title"><span>Copenhagen Cottage Market</span></h3></div>
      <div class="footer">
        <div class="field field--name-scheduled-dates field--type-date-recur field--label-hidden field__items">
          <div class="field__item">Next time <br><div class="date-text">Fri. 18 Sep. 2026 At 11.00 - 18.00</div></div>
        </div>
      </div>
    </div>
  </a>
</div>
<div class="node node--type-event node--promoted card-grid-item">
  <a href="/en/whats-on/x-international-music-festival">
    <div class="node__content">
      <div class="pre-title">Union</div>
      <div class="title"><h3 class="title"><span>X International Music Festival</span></h3></div>
      <div class="footer">
        <div class="field field--name-scheduled-dates field--type-date-recur field--label-hidden field__items">
          <div class="field__item"><div class="date-text"><span class="date">18.09.2026 - 26.09.2026</span></div></div>
        </div>
      </div>
    </div>
  </a>
</div>
</body>
</html>
"""


def test_brugbyen_extractor():
    events = BrugbyenExtractor().extract(SAMPLE_BRUGBYEN_HTML, "https://brugbyen.kk.dk/en/whats-on")
    assert len(events) == 2

    single_day = events[0]
    assert single_day.title == "Copenhagen Cottage Market"
    assert single_day.location == "Hoejbro Plads"
    assert single_day.start_time == datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)
    assert single_day.url == "https://brugbyen.kk.dk/en/whats-on/copenhagen-cottage-market"

    multi_day = events[1]
    assert multi_day.title == "X International Music Festival"
    assert multi_day.start_time == datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)


def test_brugbyen_with_date_window():
    url = with_brugbyen_date_window("https://brugbyen.kk.dk/en/whats-on", 7)
    assert "dates%5Bmin%5D=" in url
    assert "dates%5Bmax%5D=" in url


def _make_mock_client(post_response=None, get_responses=None):
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    if post_response is not None:
        mock_client.post.return_value = post_response
    if get_responses is not None:
        mock_client.get.side_effect = get_responses
    return mock_client


def _mock_response(json_data, status_code=200, raise_error=None):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    if raise_error:
        resp.raise_for_status = MagicMock(side_effect=raise_error)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def test_brightdata_facebook_fetch_events_parses_records():
    # Field names below match a real captured "discover_new" response --
    # this shape (title/event_start_time/nested location+description
    # objects) is completely different from the public docs' example, which
    # turned out to describe the default "collect one specific event" mode
    # instead.
    scrape_resp = _mock_response(
        [
            {
                "title": "Test Concert",
                "event_start_time": "2026-09-20T19:00:00.000Z",
                "event_end_time": "2026-09-20T22:00:00.000Z",
                "location": {"address": "Test Venue", "url": None},
                "url": "https://www.facebook.com/events/12345",
                "description": {"text": "A test event"},
                "main_image_downloadable": "https://example.com/image.jpg",
                "tickets": {"url": "https://example.com/tickets"},
            },
            {"title": "Missing date event"},
        ]
    )
    mock_client = _make_mock_client(post_response=scrape_resp)

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client):
        events = brightdata_facebook.fetch_events("fake-key", "https://www.facebook.com/events/search/?q=Copenhagen")

    # This scraper hits POST /datasets/v3/scrape directly (a blocking call
    # that returns results in the response body) -- not the generic
    # trigger/progress/snapshot flow used by other Bright Data datasets.
    # The URL list must be wrapped in an "input" object, not a bare array.
    call_args, post_kwargs = mock_client.post.call_args
    assert call_args[0] == "https://api.brightdata.com/datasets/v3/scrape"
    assert post_kwargs["params"] == {
        "dataset_id": "gd_m14sd0to1jz48ppm51",
        "notify": "false",
        "include_errors": "true",
        "type": "discover_new",
        "discover_by": "url",
    }
    assert post_kwargs["json"] == {
        "input": [{"url": "https://www.facebook.com/events/search/?q=Copenhagen"}],
        "limit_per_input": None,
    }

    assert len(events) == 1
    assert events[0].title == "Test Concert"
    assert events[0].start_time == datetime(2026, 9, 20, 19, 0, tzinfo=timezone.utc)
    assert events[0].end_time == datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    assert events[0].location == "Test Venue"
    assert events[0].description == "A test event"
    assert events[0].image_url == "https://example.com/image.jpg"
    assert events[0].url == "https://www.facebook.com/events/12345"


def test_brightdata_facebook_falls_back_to_async_on_202():
    # Bright Data's /scrape endpoint has a ~1 minute synchronous window;
    # past that it returns 202 with a snapshot_id instead of the data, and
    # the client has to fall back to the progress/snapshot polling flow.
    scrape_resp = _mock_response({"snapshot_id": "s_test123"}, status_code=202)
    progress_resp = _mock_response({"status": "ready"})
    snapshot_resp = _mock_response(
        [
            {
                "title": "Async Concert",
                "event_start_time": "2026-09-21T20:00:00.000Z",
                "location": {"address": "Async Venue"},
                "url": "https://www.facebook.com/events/999",
            }
        ]
    )
    mock_client = _make_mock_client(post_response=scrape_resp, get_responses=[progress_resp, snapshot_resp])

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client), \
         patch("app.services.scrapers.brightdata_facebook.time.sleep"):
        events = brightdata_facebook.fetch_events("fake-key", "https://www.facebook.com/events/search/?q=Copenhagen")

    assert len(events) == 1
    assert events[0].title == "Async Concert"
    assert events[0].location == "Async Venue"
    mock_client.get.assert_any_call(
        "https://api.brightdata.com/datasets/v3/progress/s_test123",
        headers={"Authorization": "Bearer fake-key", "Content-Type": "application/json"},
    )


def test_brightdata_facebook_raises_on_202_without_snapshot_id():
    scrape_resp = _mock_response({}, status_code=202)
    mock_client = _make_mock_client(post_response=scrape_resp)

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client):
        with pytest.raises(brightdata_facebook.BrightDataError):
            brightdata_facebook.fetch_events("fake-key", "https://www.facebook.com/events/search/?q=Copenhagen")


def test_brightdata_facebook_fetch_single_event():
    scrape_resp = _mock_response(
        [
            {
                "title": "Single Event Lookup",
                "event_start_time": "2026-11-01T18:00:00.000Z",
                "location": {"address": "Test Hall"},
                "url": "https://www.facebook.com/events/555/",
                "tickets": {"min_price": 100, "max_price": 150, "currency": "DKK"},
            }
        ]
    )
    mock_client = _make_mock_client(post_response=scrape_resp)

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client):
        event = brightdata_facebook.fetch_single_event("fake-key", "https://www.facebook.com/events/555/")

    # The single-event "collect" mode omits type=discover_new&discover_by=url
    # (those are specific to the search-discovery flow in fetch_events()).
    call_args, post_kwargs = mock_client.post.call_args
    assert post_kwargs["params"] == {
        "dataset_id": "gd_m14sd0to1jz48ppm51",
        "notify": "false",
        "include_errors": "true",
    }

    assert event is not None
    assert event.title == "Single Event Lookup"
    assert event.location == "Test Hall"
    assert event.cost == "DKK 100-150"


def test_brightdata_facebook_fetch_single_event_returns_none_when_empty():
    scrape_resp = _mock_response([])
    mock_client = _make_mock_client(post_response=scrape_resp)

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client):
        event = brightdata_facebook.fetch_single_event("fake-key", "https://www.facebook.com/events/555/")

    assert event is None


def test_brightdata_facebook_raises_on_http_error():
    import httpx as httpx_module

    error_resp = _mock_response({"error": "Unauthorized"}, status_code=401)
    error_resp.raise_for_status.side_effect = httpx_module.HTTPStatusError(
        "401", request=MagicMock(), response=error_resp
    )
    mock_client = _make_mock_client(post_response=error_resp)

    with patch("app.services.scrapers.brightdata_facebook.httpx.Client", return_value=mock_client):
        with pytest.raises(brightdata_facebook.BrightDataError):
            brightdata_facebook.fetch_events("fake-key", "https://www.facebook.com/events/search/?q=Copenhagen")


def test_ics_feed_extractor():
    extractor = IcsFeedExtractor()
    events = extractor.extract(SAMPLE_ICS, "https://example.com/calendar.ics")
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "London Python Dev Meetup"
    assert "Skills Matter" in ev.location
    assert ev.start_time == datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
