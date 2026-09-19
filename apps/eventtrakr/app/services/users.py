from __future__ import annotations

import logging
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import CatalogSource, User
from app.services import passwords

logger = logging.getLogger("eventtrakr.users")

DEFAULT_CATALOG = [
    {
        "name": "Eventbrite Copenhagen",
        "url": "https://www.eventbrite.dk/d/denmark--copenhagen/all-events/",
        "category": "General",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Broad Copenhagen event listings from Eventbrite Denmark.",
    },
    {
        "name": "Luma Copenhagen",
        "url": "https://luma.com/copenhagen",
        "category": "Community",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Community events, founder meetups, talks, and social gatherings around Copenhagen.",
    },
    {
        "name": "BrugByen Copenhagen",
        "url": "https://brugbyen.kk.dk/en/whats-on",
        "category": "Community",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "City of Copenhagen's own cultural events guide.",
    },
    {
        "name": "Bandsintown Copenhagen",
        "url": "https://www.bandsintown.com/c/copenhagen-denmark/choose-dates/genre/all-genres",
        "category": "Music",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Concert and live music listings for Copenhagen from Bandsintown.",
    },
    {
        "name": "Songkick Copenhagen",
        "url": "https://www.songkick.com/metro-areas/28617-denmark-copenhagen",
        "category": "Music",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Concert, festival, and tour date listings for Copenhagen from Songkick.",
    },
    {
        "name": "Copenhagen Post Calendar",
        "url": "https://cphpost.dk/calendar/?ArrKunstner=&Area=Kbh.+og+Frederiksberg&Rating=1",
        "category": "Culture",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "English-language Copenhagen calendar from The Copenhagen Post.",
    },
    {
        "name": "Kultunaut Copenhagen",
        "url": "https://www.kultunaut.dk/perl/arrlist/type-nynaut?showmap=&Area=Kbh.+og+Frederiksberg&nearmeradius=2000&Genre=",
        "category": "Culture",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Danish cultural calendar with events across Copenhagen and nearby areas.",
    },
    {
        "name": "MigogKbh Kalender",
        "url": "https://migogkbh.dk/kalender/",
        "category": "Community",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Local Copenhagen happenings, culture, food, and family-friendly events.",
    },
    {
        "name": "Madbillet Copenhagen",
        "url": "https://madbillet.dk/show/index/english",
        "category": "Food & Drink",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "supported",
        "description": "Food events, dinners, tastings, and culinary experiences in Copenhagen.",
    },
    {
        "name": "Facebook Copenhagen Events",
        "url": "https://www.facebook.com/events/search/?q=Copenhagen",
        "category": "Community",
        "city_or_region": "Copenhagen, Denmark",
        "source_type": "generic",
        "description": "Facebook event search for Copenhagen. Requires a Bright Data API key under Settings > Data Providers -- Facebook's own search page needs a login our scraper can't provide.",
    },
]

LEGACY_CATALOG_URLS = {
    "https://www.meetup.com/find/?keywords=tech&location=gb--London&source=EVENTS",
    "https://www.eventbrite.co.uk/d/united-kingdom--london/music--events/",
    "https://www.eventbrite.co.uk/d/united-kingdom--london/arts--events/",
    "https://www.meetup.com/find/?keywords=social&location=gb--London&source=EVENTS",
    "https://www.eventbrite.co.uk/d/united-kingdom--london/food-and-drink--events/",
    # Anders & Kaitlin Events -- no longer active
    "https://andersandkaitlin.com/events",
    # Madbillet Copenhagen -- old URL replaced by /show/index/english
    "https://www.madbillet.dk/events/copenhagen",
    # Kultunaut Copenhagen -- old URL had no area/date filter, so its default
    # (unfiltered, nationwide) result set didn't match the page's own markup
    # closely enough for the extractor to find anything worth keeping
    "https://www.kultunaut.dk/perl/arrlist/type-nynaut",
    # Copenhagen Post Calendar -- old URL had no area/rating filter
    "https://cphpost.dk/calendar/",
    # VisitDenmark Copenhagen Events -- VisitDenmark no longer runs an
    # events calendar
    "https://www.visitdenmark.dk/danmark/explore/events-copenhagen",
    # VisitCopenhagen Events -- backed by a cookie-gated, token-authenticated
    # cruncho.co SPA with no stable markup or direct event URLs; not worth
    # scraping reliably, dropped rather than fixed
    "https://www.visitcopenhagen.com/copenhagen/activities/events",
    # Resident Advisor Copenhagen -- ra.co's own bot-detection (DataDome)
    # blocks the headless fetch with a CAPTCHA challenge before our
    # ResidentAdvisorExtractor (kept -- it's correct) ever sees real content
    "https://ra.co/events/dk/copenhagen",
    # Facebook Copenhagen Events -- /search/events/ is what the logged-in
    # browser UI shows, but Bright Data's discovery scraper is specifically
    # built around the /events/search/ path (confirmed via the dashboard's
    # own discovery example); /search/events/ is the wrong one here
    "https://www.facebook.com/search/events/?q=Copenhagen",
}


def ensure_admin_user() -> User:
    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.role == "admin")).scalar_one_or_none()
        if admin:
            return admin
        hashed = passwords.hash_password(env.admin_password or "admin")
        admin = User(
            username=env.admin_username or "admin",
            password=hashed,
            role="admin",
            is_public=False,
            default_location=env.default_location,
            active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        logger.info("Created default admin user '%s'", admin.username)
        return admin


def using_factory_admin() -> bool:
    """True when the first admin account still accepts the factory password."""
    with SessionLocal() as db:
        admin = (
            db.execute(select(User).where(User.role == "admin").order_by(User.id.asc()))
            .scalars()
            .first()
        )
        if admin is None:
            return False
        expected_user = (env.admin_username or "admin").strip() or "admin"
        if (admin.username or "").strip() != expected_user:
            return False
        factory = (env.admin_password or "admin").strip() or "admin"
        return passwords.verify_password(admin.password, factory)


def get_or_create_from_platform(platform) -> User:
    auth_id = str(getattr(platform, "user_id", "") or "")
    username = (getattr(platform, "username", "") or "").strip()
    if not auth_id or not username:
        raise ValueError("Platform user is missing an id or username.")
    is_admin = bool(getattr(platform, "is_admin", False))
    desired_role = "admin" if is_admin else "user"
    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.auth_user_id == auth_id)).scalar_one_or_none()
        if existing is not None:
            if existing.role != desired_role:
                existing.role = desired_role
                db.commit()
                db.refresh(existing)
            db.expunge(existing)
            return existing
        by_name = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if by_name is not None:
            if not by_name.auth_user_id:
                by_name.auth_user_id = auth_id
                by_name.role = desired_role
                db.commit()
                db.refresh(by_name)
                db.expunge(by_name)
                return by_name
            username = f"{username}-{auth_id[:8]}"
        user = User(
            username=username[:80],
            password="",
            role=desired_role,
            default_location=env.default_location,
            active=True,
            auth_user_id=auth_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def seed_default_catalog() -> None:
    with SessionLocal() as db:
        for legacy_url in LEGACY_CATALOG_URLS:
            legacy = db.execute(select(CatalogSource).where(CatalogSource.url == legacy_url)).scalar_one_or_none()
            if legacy:
                legacy.is_recommended = False

        for item in DEFAULT_CATALOG:
            exists = db.execute(select(CatalogSource).where(CatalogSource.url == item["url"])).scalar_one_or_none()
            if exists:
                exists.name = item["name"]
                exists.category = item["category"]
                exists.city_or_region = item["city_or_region"]
                exists.source_type = item["source_type"]
                exists.description = item["description"]
                exists.is_recommended = True
            else:
                cat = CatalogSource(
                    name=item["name"],
                    url=item["url"],
                    category=item["category"],
                    city_or_region=item["city_or_region"],
                    source_type=item["source_type"],
                    description=item["description"],
                    is_recommended=True,
                )
                db.add(cat)
        db.commit()
