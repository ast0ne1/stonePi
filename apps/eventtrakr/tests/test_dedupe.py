from datetime import datetime, timezone
from app.services.dedupe import canonicalize_url, compute_event_fingerprint, normalize_string


def test_canonicalize_url():
    url = "https://WWW.Example.com/events/item/?utm_source=fb&utm_medium=cpc&id=123"
    canon = canonicalize_url(url)
    assert "utm_source" not in canon
    assert "utm_medium" not in canon
    assert "id=123" in canon
    assert canon.startswith("https://example.com/events/item")


def test_compute_event_fingerprint():
    dt = datetime(2026, 9, 20, 19, 30, tzinfo=timezone.utc)
    fp1 = compute_event_fingerprint(1, "Live Jazz Night!", dt, "The Blue Note, London")
    fp2 = compute_event_fingerprint(1, "live jazz night", dt, "The Blue Note London")
    assert fp1 == fp2  # normalized identically

    # Different date produces different fingerprint
    dt_diff = datetime(2026, 9, 21, 19, 30, tzinfo=timezone.utc)
    fp3 = compute_event_fingerprint(1, "Live Jazz Night!", dt_diff, "The Blue Note, London")
    assert fp1 != fp3
