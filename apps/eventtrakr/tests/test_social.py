"""Tests for Instagram social detection / match / notify."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db import SessionLocal, init_db
from app.models import Event, SocialAccount, SocialPost, User, utcnow
from app.services import notify as notify_service
from app.services import settings as settings_service
from app.services.social import config as social_config
from app.services.social import detect as detect_service
from app.services.social import discover as discover_service
from app.services.social import match as match_service
from app.services.social import process as process_service
from app.services.social.types import DetectionResult, SourceContent


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _admin_id() -> int:
    init_db()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        assert admin is not None
        return int(admin.id)


def test_detect_high_confidence_text_event():
    content = SourceContent(
        platform="instagram",
        account_username="theglobe",
        account_type="venue",
        post_url="https://www.instagram.com/p/abc/",
        published_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        caption="Friday 27 Sept — Quiz Night — 19:30 — Free entry at The Globe",
        ocr_text="",
        combined_text="Friday 27 Sept — Quiz Night — 19:30 — Free entry at The Globe",
        hashtags=["quiz"],
    )
    result = detect_service.detect_event(content)
    assert result.is_event_like
    assert result.confidence in ("high", "medium")
    assert result.start_time is not None or "Date detected" in result.signals
    assert result.cost.lower() == "free" or "free" in (result.cost or "").lower() or result.cost == "Free"


def test_detect_ignores_ordinary_social():
    content = SourceContent(
        platform="instagram",
        account_username="theglobe",
        account_type="venue",
        post_url="https://www.instagram.com/p/xyz/",
        published_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        caption="Thanks everyone for another amazing night ❤️",
        ocr_text="",
        combined_text="Thanks everyone for another amazing night ❤️",
    )
    result = detect_service.detect_event(content)
    assert result.confidence in ("none", "low")
    assert not result.is_event_like or result.confidence == "low"


def test_detect_ambiguous_becomes_low_or_medium():
    content = SourceContent(
        platform="instagram",
        account_username="theglobe",
        account_type="venue",
        post_url="https://www.instagram.com/p/amb/",
        published_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        caption="Something big is happening Friday 👀",
        ocr_text="",
        combined_text="Something big is happening Friday 👀",
    )
    result = detect_service.detect_event(content)
    assert result.confidence != "high"


def test_match_strong_against_existing_event():
    admin_id = _admin_id()
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        fp = "social-match-fp"
        db.query(Event).filter_by(fingerprint=fp).delete()
        db.commit()
        ev = Event(
            user_id=admin_id,
            fingerprint=fp,
            title="The Bluebirds Live",
            description="",
            start_time=now.replace(hour=20, minute=0),
            location="The Globe",
            cost="Free",
            category="Music",
            url="",
        )
        db.add(ev)
        db.commit()
        event_id = ev.id

        content = SourceContent(
            platform="instagram",
            account_username="theglobe",
            account_type="venue",
            post_url="https://instagram.com/p/1",
            published_at=now,
            caption="The Bluebirds this Saturday! 20:00. Free entry.",
            ocr_text="",
            combined_text="The Bluebirds this Saturday! 20:00. Free entry. The Globe",
        )
        detection = DetectionResult(
            is_event_like=True,
            confidence="high",
            score=8.0,
            signals=["test"],
            title="The Bluebirds Live",
            start_time=now.replace(hour=20, minute=0),
            location="The Globe",
        )
        match = match_service.match_existing_events(db, admin_id, content, detection, now=now)
        assert match.band == "strong"
        assert match.event_id == event_id


def test_auto_create_only_when_high_and_setting_on():
    admin_id = _admin_id()
    with SessionLocal() as db:
        social_config.set_instagram_enabled(db, True)
        social_config.set_auto_discover(db, True)
        social_config.set_auto_create_high(db, True)
        social_config.set_candidate_review(db, True)

        account = SocialAccount(
            user_id=admin_id,
            platform="instagram",
            username=_uniq("testvenue"),
            display_name="Test Venue",
            profile_url="https://www.instagram.com/testvenue/",
            account_type="venue",
            tracking_enabled=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(account)
        db.flush()
        post = SocialPost(
            social_account_id=account.id,
            external_post_id="ext-auto-1",
            post_url="https://www.instagram.com/p/ext-auto-1/",
            published_at=datetime.now(timezone.utc),
            caption="TONIGHT DJ Anna 22:00 Free entry",
            media_type="Photo",
            retrieved_at=utcnow(),
            processing_status="pending",
            combined_text="TONIGHT DJ Anna 22:00 Free entry The Globe",
        )
        db.add(post)
        db.flush()

        detection = DetectionResult(
            is_event_like=True,
            confidence="high",
            score=9.0,
            signals=["Date detected", "Time detected", "Named performer/event"],
            title="DJ Anna",
            start_time=datetime.now(timezone.utc) + timedelta(hours=4),
            location="The Globe",
            event_type="DJ",
            cost="Free",
        )
        disc, event = discover_service.auto_or_candidate(
            db, user_id=admin_id, account=account, post=post, detection=detection
        )
        db.commit()
        assert disc is not None
        assert event is not None
        assert event.origin == "instagram_discovery"

        # Medium → candidate only
        post2 = SocialPost(
            social_account_id=account.id,
            external_post_id="ext-med-1",
            post_url="https://www.instagram.com/p/ext-med-1/",
            published_at=datetime.now(timezone.utc),
            caption="Friday night vibes",
            retrieved_at=utcnow(),
            processing_status="pending",
            combined_text="Something on Friday maybe",
        )
        db.add(post2)
        db.flush()
        med = DetectionResult(
            is_event_like=True,
            confidence="medium",
            score=4.0,
            signals=["Date detected"],
            title="Friday thing",
            start_time=datetime.now(timezone.utc) + timedelta(days=1),
            location="",
        )
        disc2, event2 = discover_service.auto_or_candidate(
            db, user_id=admin_id, account=account, post=post2, detection=med
        )
        db.commit()
        assert disc2 is not None
        assert disc2.status == "candidate"
        assert event2 is None


def test_process_post_dedupe_status_skip(monkeypatch):
    admin_id = _admin_id()
    with SessionLocal() as db:
        account = SocialAccount(
            user_id=admin_id,
            platform="instagram",
            username=_uniq("skipme"),
            display_name="Skip",
            profile_url="",
            account_type="other",
            tracking_enabled=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(account)
        db.flush()
        post = SocialPost(
            social_account_id=account.id,
            external_post_id="already-done",
            post_url="",
            caption="x",
            retrieved_at=utcnow(),
            processing_status="ignored",
        )
        db.add(post)
        db.commit()
        result = process_service.process_post(db, account, post)
        assert result.get("skipped") is True


def test_social_notify_gated_by_setting(monkeypatch):
    init_db()
    emitted: list[object] = []

    def _fake_emit(envelope):
        emitted.append(envelope)
        return True

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)
    owner = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    monkeypatch.setattr(notify_service, "owner_auth_id", lambda _db, uid: owner if uid == 1 else None)

    with SessionLocal() as db:
        social_config.set_notify_enabled(db, False)
        ok = notify_service.notify_from_process_result(
            db,
            {"notify": True, "notify_kind": "discovered", "title": "DJ Anna", "event_id": 1},
            username="theglobe",
            account_user_id=1,
        )
        assert ok is False
        assert emitted == []

        social_config.set_notify_enabled(db, True)
        ok = notify_service.notify_from_process_result(
            db,
            {"notify": True, "notify_kind": "discovered", "title": "DJ Anna", "event_id": 1},
            username="theglobe",
            account_user_id=1,
        )
        assert ok is True
        assert len(emitted) == 1
        assert emitted[0].id == "eventtrakr.social_discovered"
        assert emitted[0].audience == "personal"
        assert emitted[0].user == owner


def test_confirm_discovery_creates_event():
    admin_id = _admin_id()
    with SessionLocal() as db:
        account = SocialAccount(
            user_id=admin_id,
            platform="instagram",
            username=_uniq("confirmvenue"),
            display_name="Confirm",
            profile_url="",
            account_type="venue",
            tracking_enabled=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(account)
        db.flush()
        post = SocialPost(
            social_account_id=account.id,
            external_post_id="confirm-1",
            post_url="https://instagram.com/p/c1",
            caption="Quiz",
            retrieved_at=utcnow(),
            processing_status="candidate",
            combined_text="Quiz Night",
        )
        db.add(post)
        db.flush()
        start = datetime.now(timezone.utc) + timedelta(days=2)
        detection = DetectionResult(
            is_event_like=True,
            confidence="medium",
            score=4.0,
            signals=["Date detected", "Time detected"],
            title="Saturday Quiz Night",
            start_time=start,
            location="The Globe",
            event_type="Quiz",
            cost="Free",
        )
        disc = discover_service.create_discovery(
            db,
            user_id=admin_id,
            account=account,
            post=post,
            detection=detection,
            status="candidate",
        )
        db.commit()
        disc_id = disc.id

        event = discover_service.confirm_discovery(db, disc_id, admin_id)
        assert event is not None
        assert event.title == "Saturday Quiz Night"
        assert event.origin == "instagram_discovery"


def test_normalize_username():
    assert social_config.normalize_username("@TheGlobe/") == "theglobe"
    assert social_config.normalize_username("https://www.instagram.com/theglobe") == "theglobe"


def test_purge_expired_events_clears_social_references():
    from app.models import EventDiscovery, EventSocialLink, EventUpdate
    from app.services import ingest

    admin_id = _admin_id()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    with SessionLocal() as db:
        account = SocialAccount(
            user_id=admin_id,
            platform="instagram",
            username=_uniq("purgevenue"),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(account)
        db.flush()
        post = SocialPost(social_account_id=account.id, external_post_id=_uniq("purge"), retrieved_at=utcnow())
        db.add(post)
        db.flush()
        event = Event(user_id=admin_id, fingerprint=_uniq("fp"), title="Past linked event", start_time=old)
        db.add(event)
        db.flush()
        db.add(EventSocialLink(event_id=event.id, social_post_id=post.id))
        db.add(EventUpdate(event_id=event.id, social_post_id=post.id, summary="Moved"))
        disc = EventDiscovery(
            user_id=admin_id, social_post_id=post.id, social_account_id=account.id, event_id=event.id
        )
        db.add(disc)
        db.commit()
        event_id, disc_id = event.id, disc.id

    with SessionLocal() as db:
        assert ingest.purge_expired_events(db) >= 1

    with SessionLocal() as db:
        assert db.get(Event, event_id) is None
        assert db.query(EventSocialLink).filter_by(event_id=event_id).count() == 0
        assert db.query(EventUpdate).filter_by(event_id=event_id).count() == 0
        kept = db.get(EventDiscovery, disc_id)
        assert kept is not None
        assert kept.event_id is None
