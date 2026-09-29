from stonepi_contracts import (
    DESTINATION_TRMNL,
    EventEnvelope,
    normalize_severity,
    validate_event,
    widget_by_id,
)
from stonepi_contracts.capabilities import CAPABILITY_QR, destination_capabilities


def test_validate_event_ok():
    raw = validate_event(
        {
            "id": "pricewatch.target_reached",
            "source": "pricewatch",
            "title": "Deal",
            "summary": "Cheap",
            "severity": "SUCCESS",
        }
    )
    assert raw is not None
    assert raw["severity"] == "success"
    assert raw["timestamp"]


def test_validate_event_rejects_empty():
    assert validate_event({}) is None
    assert validate_event(None) is None


def test_envelope_to_dict():
    ev = EventEnvelope(id="x.y", source="x", title="T")
    d = ev.to_dict()
    assert d["id"] == "x.y"
    assert normalize_severity("nope") == "info"


def test_widget_catalog_shim():
    w = widget_by_id("fileserve.published_files")
    assert w is not None
    assert w.supports_qr is True


def test_trmnl_caps():
    caps = destination_capabilities(DESTINATION_TRMNL)
    assert CAPABILITY_QR in caps


def test_audience_defaults_to_household():
    raw = validate_event({"id": "studio.site_published", "source": "studio", "title": "T"})
    assert raw["audience"] == "household"
    assert raw["user"] is None


def test_unknown_audience_normalizes_to_household():
    raw = validate_event({"id": "x.y", "source": "x", "title": "T", "audience": "everyone"})
    assert raw["audience"] == "household"


def test_personal_event_keeps_user():
    raw = validate_event(
        {"id": "pricewatch.price_drop", "source": "pricewatch", "title": "T",
         "audience": "personal", "user": " 7d1c-uuid "}
    )
    assert raw["audience"] == "personal"
    assert raw["user"] == "7d1c-uuid"


def test_personal_event_without_user_is_dropped():
    base = {"id": "pricewatch.price_drop", "source": "pricewatch", "title": "T", "audience": "personal"}
    assert validate_event(base) is None
    assert validate_event({**base, "user": "local"}) is None
    assert validate_event({**base, "user": "  "}) is None


def test_envelope_carries_audience_and_user():
    ev = EventEnvelope(id="x.y", source="x", title="T", audience="personal", user="abc")
    d = ev.to_dict()
    assert d["audience"] == "personal"
    assert d["user"] == "abc"


def test_emit_event_rejects_personal_without_user(monkeypatch):
    from stonepi_contracts import emit as emit_mod

    calls = []
    monkeypatch.setattr(emit_mod, "_post_once", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(emit_mod, "_enqueue", lambda payload: calls.append(payload))
    ok = emit_mod.emit_event({"id": "x.y", "source": "x", "title": "T", "audience": "personal"})
    assert ok is False
    assert calls == []


def test_event_catalog():
    from stonepi_contracts import EVENT_CATALOG, AUDIENCES, event_type, events_for_app, grouped_catalog

    ids = [e.id for e in EVENT_CATALOG]
    assert len(ids) == len(set(ids))
    for ev in EVENT_CATALOG:
        assert ev.audience in AUDIENCES
        assert ev.id.startswith(f"{ev.app}.")
    assert event_type("system.disk_warning").audience == "admin"
    assert event_type("nope.nope") is None
    assert {e.id for e in events_for_app("pinboard")} == {"pinboard.reminder_due", "pinboard.notice_posted"}
    groups = grouped_catalog()
    assert groups[0]["id"] == "newscast"
    assert groups[0]["label"] == "NewsCast"
    assert sum(len(g["events"]) for g in groups) == len(EVENT_CATALOG)


def test_local_integer_owner_is_rejected():
    base = {"id": "newscast.push_available", "source": "newscast", "title": "T", "audience": "personal"}
    assert validate_event({**base, "user": 7}) is None
    assert validate_event({**base, "user": "7"}) is None
    assert validate_event({**base, "user": "7c9e6679-7425"})["user"] == "7c9e6679-7425"


def test_emit_event_does_nothing_under_pytest(monkeypatch):
    from stonepi_contracts import emit as emit_mod

    calls = []
    monkeypatch.setattr(emit_mod, "_post_once", lambda *a, **k: calls.append("post") or True)
    monkeypatch.setattr(emit_mod, "_enqueue", lambda payload: calls.append("queue"))
    monkeypatch.delenv("STONEPI_EMIT_IN_TESTS", raising=False)
    assert emit_mod.emit_event({"id": "x.y", "source": "x", "title": "T"}) is False
    assert calls == []
    monkeypatch.setenv("STONEPI_EMIT_IN_TESTS", "1")
    assert emit_mod.emit_event({"id": "x.y", "source": "x", "title": "T"}) is True
    assert calls == ["post"]


def test_drain_can_deliver_locally(monkeypatch, tmp_path):
    import json

    from stonepi_contracts import emit as emit_mod

    queue = tmp_path / "q.jsonl"
    queue.write_text("\n".join(json.dumps({"id": f"x.{i}", "source": "x", "title": "T"}) for i in range(3)) + "\n")
    monkeypatch.setattr(emit_mod, "RETRY_QUEUE", queue)
    monkeypatch.setattr(emit_mod, "_post_once", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP")))
    got = []
    assert emit_mod.drain_emit_retry_queue(deliver=lambda p: got.append(p["id"]) or True) == 3
    assert got == ["x.0", "x.1", "x.2"]
    assert not queue.exists()
