"""state._cached: a failed fetch with nothing older to show is retried soon, not after the full TTL."""

from __future__ import annotations

from app import state


def test_failed_fetch_retries_after_a_minute(monkeypatch):
    state.clear_cache()
    clock = {"t": 1000.0}
    monkeypatch.setattr(state.time, "monotonic", lambda: clock["t"])
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("slow")
        return {"temp": 12}

    assert state._cached("weather:x", 900.0, flaky) is None
    clock["t"] += 30
    assert state._cached("weather:x", 900.0, flaky) is None and len(calls) == 1  # still waiting
    clock["t"] += 31
    assert state._cached("weather:x", 900.0, flaky) == {"temp": 12} and len(calls) == 2
    clock["t"] += 600
    assert state._cached("weather:x", 900.0, flaky) == {"temp": 12} and len(calls) == 2  # full TTL again
    state.clear_cache()


def test_failure_keeps_the_last_good_value(monkeypatch):
    state.clear_cache()
    clock = {"t": 1000.0}
    monkeypatch.setattr(state.time, "monotonic", lambda: clock["t"])
    assert state._cached("feed:a", 60.0, lambda: {"ok": True}) == {"ok": True}
    clock["t"] += 61

    def down():
        raise ConnectionError("down")

    assert state._cached("feed:a", 60.0, down) == {"ok": True}
    state.clear_cache()
