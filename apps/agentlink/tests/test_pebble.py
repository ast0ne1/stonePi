"""AgentLink Pebble adapter against PROTOCOL.md v1: claim, config, mood, ask, unpair, Sidekick."""

from __future__ import annotations

import array
import io
import json
import math
import threading
import time
import wave
from datetime import datetime
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal_v2

from app import ask, deps, internal, mood, pebble, people, registry, settings, status
from app.config import env
from app.main import app

SECRET = "agentlink-test-secret"
DEVICE = "pbl-a0b1c2d3e4f5"
B = pebble.BASE
WATCH = {"level": "healthy", "summary": "All clear", "reasons": [], "apps": [{"id": "news", "n": "NewsCast", "running": True}],
         "disk_pct": 41, "backup_age_days": 1.2}


class FakeResult:
    def __init__(self, text):
        self.text = text


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    deps.reset(tmp_path)
    pebble.reset_runtime()
    monkeypatch.setattr(env, "default_path", "lan")
    monkeypatch.setattr(env, "speech", "dev")
    monkeypatch.setattr(env, "lan_url", "http://192.168.1.20")
    monkeypatch.setattr(env, "away_url", "https://stonepi.tail1234.ts.net:8443")
    monkeypatch.setattr(pebble, "CLAIM_MIN_INTERVAL_S", 0)
    monkeypatch.setattr(pebble, "MOOD_MIN_INTERVAL_S", 0)
    monkeypatch.setattr(internal, "session_secret", lambda: SECRET)
    monkeypatch.setattr(people, "user_enabled", lambda _uid: None)
    monkeypatch.setattr(pebble, "session_secret", lambda: "")  # dev: no Auth to ask
    monkeypatch.setattr(status, "watch", lambda: WATCH)
    monkeypatch.setattr(people, "user_apps", lambda _uid: set())
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    mood.reset_state()
    import stonepi_ai

    calls = []

    def fake_complete(function_id, **kwargs):
        calls.append((function_id, kwargs))
        return FakeResult("All good. **Everything** is running, see https://x.example")

    monkeypatch.setattr(stonepi_ai, "complete", fake_complete)
    yield calls


# As on the Pi, requests arrive from loopback (nginx / Funnel), so X-StonePi-Path is trusted.
client = TestClient(app, client=("127.0.0.1", 50000))


def signed(path: str, body: dict | None = None):
    raw = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(SECRET, "POST", path, raw)}
    return client.post(path, content=raw, headers=headers)


def poll(code="K7M4", device=DEVICE, headers=None):
    return client.post(f"{B}/claim/poll", json={"code": code, "device_id": device, "fw": "0.1.0", "model": "waveshare"},
                       headers=headers or {})


def paired(nickname="Kitchen") -> tuple[str, dict]:
    assert poll().status_code == 202
    r = signed(f"{internal.PREFIX}/pebble/claim", {"code": "K7M4", "user_id": "u1", "user_name": "Adam", "nickname": nickname})
    assert r.status_code == 200, r.text
    r = poll()
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()


def wav(seconds, rate=16000, amp=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", (int(amp * math.sin(i / 8)) for i in range(int(seconds * rate)))).tobytes())
    return buf.getvalue()


def auth(token):
    return {"Authorization": f"Bearer {token}", "X-Pebble-Device": DEVICE, "X-Pebble-Fw": "0.1.1", "X-Pebble-Battery": "87"}


# -- claim -----------------------------------------------------------------------------


def test_claim_flow_and_codes():
    assert poll(code="K0M4").status_code == 400  # 0 isn't in the alphabet
    assert poll(device="pbl-xyz").status_code == 400
    r = poll()
    assert r.status_code == 202 and r.json()["status"] == "waiting"
    assert poll(device="pbl-ffffffffffff").json()["error"] == "code_in_use"
    # Sidekick can only claim a code a device is showing.
    r = signed(f"{internal.PREFIX}/pebble/claim", {"code": "AAAA", "user_id": "u1", "user_name": "Adam"})
    assert r.status_code == 404
    token, body = paired()
    assert token.startswith("pbt_") and len(token) == 47
    assert body["owner"] == "Adam" and body["config"]["nickname"] == "Kitchen"
    assert body["config"]["hosts"] == {"lan": "http://192.168.1.20", "away": None}
    assert poll().json()["error"] == "code_expired"  # token handed out once


def test_claim_refused_over_away_and_rate_limited(monkeypatch):
    assert poll(headers={"X-StonePi-Path": "away"}).status_code == 404
    monkeypatch.setattr(pebble, "CLAIM_MIN_INTERVAL_S", 1.0)
    poll(code="ACDE")
    r = poll(code="ACDE")
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"


def test_unsigned_internal_is_404():
    r = client.post(f"{internal.PREFIX}/pebble/claim", json={"code": "K7M4", "user_id": "u1"})
    assert r.status_code == 404


def test_reclaim_revokes_old_token_and_keeps_name():
    old, _ = paired(nickname="Desk")
    pebble.reset_runtime()
    new, body = paired(nickname="")
    assert body["config"]["nickname"] == "Desk"
    assert client.get(f"{B}/config", headers=auth(old)).status_code == 401
    assert client.get(f"{B}/config", headers=auth(new)).status_code == 200


# -- config / mood / paths ----------------------------------------------------------------


def test_config_rev_and_away_toggle():
    token, body = paired()
    rev = body["config"]["rev"]
    assert client.get(f"{B}/mood", headers={**auth(token), "X-StonePi-Path": "away"}).json()["error"] == "away_disabled"
    overview = client.get(f"{internal.PREFIX}/pebble/overview", headers=sign_internal_v2(SECRET, "GET", f"{internal.PREFIX}/pebble/overview")).json()
    binding_id = overview["devices"][0]["id"]
    assert overview["devices"][0]["fw"] == "0.1.0"
    r = signed(f"{internal.PREFIX}/pebble/device", {"id": binding_id, "away_allowed": True, "nickname": "Hall"})
    assert r.json()["ok"]
    cfg = client.get(f"{B}/config", headers=auth(token)).json()
    assert cfg["rev"] != rev and cfg["nickname"] == "Hall"
    assert cfg["hosts"]["away"] == "https://stonepi.tail1234.ts.net:8443"
    m = client.get(f"{B}/mood", headers={**auth(token), "X-StonePi-Path": "away"})
    assert m.status_code == 200 and m.json()["config_rev"] == cfg["rev"]
    # The device's headers were recorded.
    row = client.get(f"{internal.PREFIX}/pebble/overview", headers=sign_internal_v2(SECRET, "GET", f"{internal.PREFIX}/pebble/overview")).json()["devices"][0]
    assert row["fw"] == "0.1.1" and row["battery"] == "87" and row["last_path"] == "away"


def test_missing_path_header_defaults_to_away(monkeypatch):
    token, _ = paired()
    monkeypatch.setattr(env, "default_path", "away")
    assert client.get(f"{B}/config", headers=auth(token)).json()["error"] == "away_disabled"


def test_talk_settings_need_one_way():
    token, _ = paired()
    bid = deps.store().list()[0]["id"]
    r = signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "talk": {"hold": False, "toggle": False, "wake": False}})
    assert r.status_code == 400
    signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "talk": {"hold": False, "toggle": True, "wake": True, "wake_phrase": "hey pebble"}})
    assert client.get(f"{B}/config", headers=auth(token)).json()["talk"] == {"hold": False, "toggle": True, "wake": True, "wake_phrase": "hey pebble"}


NOON = datetime(2026, 10, 24, 12, 0)
NIGHT = datetime(2026, 10, 24, 23, 0)


def _watch(reason="", level="attention", app=("newscast", "NewsCast")):
    rows = [{"id": app[0], "n": app[1], "running": not reason.endswith("running")}]
    return {**WATCH, "level": level if reason else "healthy", "reasons": [reason] if reason else [], "apps": rows}


@pytest.mark.parametrize(
    ("reason", "last_ok_ago", "battery", "clock", "expected"),
    [
        ("Auth is down", None, "", NOON, "upset"),
        ("Backup failed", 60, "", NIGHT, "upset"),
        ("Disk 96% full", None, "5", NOON, "upset"),
        ("", 60, "", NOON, "proud"),
        ("", 3600, "", NOON, "happy"),
        ("", 5 * 3600, "", NOON, "content"),
        ("", None, "", NOON, "content"),  # no neglect-sleepy any more
        ("", 60, "", NIGHT, "sleepy"),
        ("", 60, "12", NOON, "sleepy"),
    ],
)
def test_mood_rules(monkeypatch, reason, last_ok_ago, battery, clock, expected):
    from app import mood

    monkeypatch.setattr(status, "watch", lambda: _watch(reason, "critical"))
    now = 1_000_000.0
    binding = {"rev": 2, "battery": battery, "last_ask_ok": (now - last_ok_ago) if last_ok_ago else None}
    out = mood.compute(binding, now=now, local_now=clock)
    assert out["mood"] == expected and out["mood"] in mood.MOODS and out["config_rev"] == 2
    assert len(out["caption"]) <= 40 and out["reason"]


def test_worry_waits_and_only_for_the_owners_apps(monkeypatch):
    from app import mood

    monkeypatch.setattr(people, "user_apps", lambda _uid: {"newscast"})
    monkeypatch.setattr(status, "watch", lambda: _watch("NewsCast is not running"))
    binding = {"user_id": "u1", "rev": 1}
    assert mood.decide(binding, now=1000.0, local_now=NOON)[0] == "content"  # a blip
    mood_, reason, caption = mood.decide(binding, now=1000.0 + mood.WORRY_AFTER_S, local_now=NOON)
    assert mood_ == "worried" and reason == caption == "NewsCast is not running"
    # Someone else's app being down isn't this owner's worry; disk and backup always are.
    monkeypatch.setattr(status, "watch", lambda: _watch("PriceWatch is not running", app=("pricewatch", "PriceWatch")))
    assert mood.decide(binding, now=5000.0, local_now=NOON)[0] == "content"
    assert mood.decide(binding, now=5000.0 + mood.WORRY_AFTER_S, local_now=NOON)[0] == "content"
    monkeypatch.setattr(status, "watch", lambda: _watch("Backup 9d ago"))
    mood.decide(binding, now=9000.0, local_now=NOON)
    assert mood.decide(binding, now=9000.0 + mood.WORRY_AFTER_S, local_now=NOON)[0] == "worried"
    # Worry beats quiet hours; quiet hours beat proud.
    assert mood.decide(binding, now=9000.0 + mood.WORRY_AFTER_S, local_now=NIGHT)[0] == "worried"


def test_quiet_hours_setting(monkeypatch):
    from app import mood, settings

    binding = {"rev": 1, "last_ask_ok": 999_990.0}
    assert mood.decide(binding, now=1_000_000.0, local_now=NIGHT)[0] == "sleepy"
    assert settings.set_quiet_hours(True, "25:00", "07:00")[0] is False
    assert settings.set_quiet_hours(True, "09:00", "09:00")[0] is False
    assert settings.set_quiet_hours(True, "11:00", "13:00")[0] is True
    assert mood.decide(binding, now=1_000_000.0, local_now=NOON)[0] == "sleepy"
    assert mood.decide(binding, now=1_000_000.0, local_now=NIGHT)[0] == "proud"
    settings.set_quiet_hours(False, "11:00", "13:00")
    assert mood.decide(binding, now=1_000_000.0, local_now=NOON)[0] == "proud"


def test_mood_settles_before_changing_face(monkeypatch):
    from app import mood

    binding = {"id": 991, "rev": 1, "last_ask_ok": None}
    first = mood.compute(binding, now=1_000_000.0, local_now=NOON)
    later = mood.compute(binding, now=1_000_600.0, local_now=NOON)
    assert first["mood"] == later["mood"] == "content" and later["since"] == first["since"]
    # Quiet hours begin: the face waits SETTLE_S before turning sleepy.
    assert mood.compute(binding, now=1_000_700.0, local_now=NIGHT)["mood"] == "content"
    assert mood.compute(binding, now=1_000_700.0 + mood.SETTLE_S, local_now=NIGHT)["mood"] == "sleepy"
    # Upset shows at once.
    monkeypatch.setattr(status, "watch", lambda: _watch("Auth is down", "critical"))
    changed = mood.compute(binding, now=1_000_800.0 + mood.SETTLE_S, local_now=NIGHT)
    assert changed["mood"] == "upset" and changed["since"] != first["since"]


def test_mood_rate_limit(monkeypatch):
    token, _ = paired()
    monkeypatch.setattr(pebble, "MOOD_MIN_INTERVAL_S", 10)
    assert client.get(f"{B}/mood", headers=auth(token)).status_code == 200
    r = client.get(f"{B}/mood", headers=auth(token))
    assert r.status_code == 429 and r.json()["retry_after_s"] >= 1


def test_owner_disabled(monkeypatch):
    token, _ = paired()
    monkeypatch.setattr(people, "user_enabled", lambda _uid: False)
    r = client.get(f"{B}/config", headers=auth(token))
    assert r.status_code == 403 and r.json()["error"] == "owner_disabled"


# -- ask ---------------------------------------------------------------------------------


def test_ask_dev_pipeline(isolated):
    token, _ = paired()
    r = client.post(f"{B}/ask", content=wav(2.0), headers={**auth(token), "Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 22050 and w.getnchannels() == 1
    assert unquote(r.headers["X-Pebble-Transcript"]) == "How is StonePi doing?"
    reply = unquote(r.headers["X-Pebble-Reply"])
    assert "**" not in reply and "https://" not in reply and reply.startswith("All good.")
    function_id, kwargs = isolated[-1]
    assert function_id == "pebble.reply"
    assert '"get_status"' in kwargs["system"] and "Adam" in kwargs["system"]
    # Mood turns proud after a good ask; the audit has the ask and the get_status it used, no words.
    assert client.get(f"{B}/mood", headers=auth(token)).json()["mood"] == "proud"
    actions = [row["action"] for row in deps.audit().query()]
    assert "ask" in actions and "get_status" in actions
    assert all("How is" not in json.dumps(row["params"]) for row in deps.audit().query())


@pytest.mark.parametrize(
    ("body", "ctype", "status_code", "error"),
    [
        (wav(1.0, rate=22050), "audio/wav", 415, "unsupported_audio"),
        (wav(1.0), "text/plain", 415, "unsupported_audio"),
        (b"not a wav", "audio/wav", 415, "unsupported_audio"),
        (wav(1.0, amp=0), "audio/wav", 422, "no_speech"),
        (wav(17.0), "audio/wav", 413, "too_large"),
    ],
    ids=["22k", "text-plain", "garbage", "silence", "17s"],
)
def test_ask_errors(body, ctype, status_code, error):
    token, _ = paired()
    r = client.post(f"{B}/ask", content=body, headers={**auth(token), "Content-Type": ctype})
    assert r.status_code == status_code and r.json()["error"] == error


def test_ask_ai_unavailable(monkeypatch):
    import stonepi_ai

    def boom(*_a, **_k):
        raise stonepi_ai.AIUnavailable("none", reason="no_models")

    monkeypatch.setattr(stonepi_ai, "complete", boom)
    token, _ = paired()
    r = client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    assert r.status_code == 503 and r.json()["error"] == "ai_unavailable" and r.headers["Retry-After"] == "60"


def test_ask_speech_unavailable(monkeypatch):
    monkeypatch.setattr(env, "speech", "off")
    token, _ = paired()
    r = client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    assert r.status_code == 503 and r.json()["error"] == "speech_unavailable"


def test_concurrent_ask_is_busy(monkeypatch):
    import stonepi_ai

    gate = threading.Event()

    def slow(*_a, **_k):
        gate.wait(5)
        return FakeResult("Done.")

    monkeypatch.setattr(stonepi_ai, "complete", slow)
    token, _ = paired()
    results = {}

    def first():
        results["a"] = client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"}).status_code

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.3)
    r = client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    gate.set()
    t.join()
    assert r.status_code == 409 and r.json()["error"] == "busy" and results["a"] == 200


def test_tool_policy_off_removes_facts(isolated):
    token, _ = paired()
    bid = deps.store().list()[0]["id"]
    signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "tools": {"get_status": False}})
    client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    assert '"get_status"' not in isolated[-1][1]["system"]
    binding = deps.store().get(bid)
    assert registry.invoke(binding, "get_status")["error"] == "not_allowed"
    assert registry.invoke(binding, "nope")["error"] == "unknown_tool"
    assert registry.invoke(binding, "get_status", {"user": "eva"})["error"] == "bad_params"


# -- revoke / unpair ---------------------------------------------------------------------------


def test_unpair_and_revoke():
    token, _ = paired()
    assert client.post(f"{B}/unpair", headers=auth(token)).status_code == 204
    assert client.get(f"{B}/config", headers=auth(token)).status_code == 401
    pebble.reset_runtime()
    token, _ = paired()
    bid = deps.store().list()[0]["id"]
    assert signed(f"{internal.PREFIX}/pebble/revoke", {"id": bid}).json()["ok"]
    assert client.get(f"{B}/mood", headers=auth(token)).json()["error"] == "unauthorized"
    assert signed(f"{internal.PREFIX}/pebble/revoke", {"id": bid}).status_code == 404


def test_unknown_path_and_health():
    assert client.get(f"{B}/nope").status_code == 404
    assert client.get("/healthz").json()["ok"]


def test_away_url_from_funnel_helper_file(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "away_url", "")
    monkeypatch.setattr(pebble, "DATA_DIR", tmp_path)
    assert pebble.away_url() == ""
    (tmp_path / "away_url").write_text("https://stonepi.tail1234.ts.net:8443/\n", encoding="utf-8")
    assert pebble.away_url() == "https://stonepi.tail1234.ts.net:8443"


def test_config_rev_follows_hosts(tmp_path, monkeypatch):
    """Funnel switching on after the settings save (or the Pi's IP changing) changes the rev."""
    token, body = paired()
    bid = deps.store().list()[0]["id"]
    monkeypatch.setattr(env, "away_url", "")
    monkeypatch.setattr(pebble, "DATA_DIR", tmp_path)
    signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "away_allowed": True})
    before = client.get(f"{B}/config", headers=auth(token)).json()
    assert before["hosts"]["away"] is None
    (tmp_path / "away_url").write_text("https://stonepi.tail1234.ts.net:8443", encoding="utf-8")
    mood_rev = client.get(f"{B}/mood", headers=auth(token)).json()["config_rev"]
    after = client.get(f"{B}/config", headers=auth(token)).json()
    assert mood_rev == after["rev"] != before["rev"]
    assert after["hosts"]["away"] == "https://stonepi.tail1234.ts.net:8443"
    monkeypatch.setattr(env, "lan_url", "http://192.168.1.99")
    assert client.get(f"{B}/config", headers=auth(token)).json()["rev"] != after["rev"]


def test_proxy_headers_ignored_from_non_loopback(monkeypatch):
    """run-dev listens on the LAN: a client can't pick its own path or IP."""
    token, _ = paired()
    lan_client = TestClient(app, client=("192.168.1.50", 50000))
    monkeypatch.setattr(env, "default_path", "away")
    r = lan_client.get(f"{B}/config", headers={**auth(token), "X-StonePi-Path": "lan"})
    assert r.json()["error"] == "away_disabled"
    monkeypatch.setattr(env, "default_path", "lan")
    assert lan_client.get(f"{B}/config", headers={**auth(token), "X-StonePi-Path": "away"}).status_code == 200


def test_internal_rejects_malformed_input():
    paired()
    assert signed(f"{internal.PREFIX}/pebble/device", {"id": "7a"}).status_code == 400
    bid = deps.store().list()[0]["id"]
    assert signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "tools": ["get_status"]}).status_code == 400
    assert signed(f"{internal.PREFIX}/pebble/device", {"id": bid, "talk": "hold"}).status_code == 400
    assert signed(f"{internal.PREFIX}/pebble/revoke", {"id": None}).status_code == 400
    assert signed(f"{internal.PREFIX}/pebble/revoke", {"id": True}).status_code == 400


def test_root_sends_people_to_sidekick(monkeypatch):
    monkeypatch.setenv("STONEPI_NOTIFY_URL", "http://127.0.0.1:8012")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "http://127.0.0.1:8012/sidekick"
    monkeypatch.delenv("STONEPI_NOTIFY_URL")
    assert client.get("/", follow_redirects=False).headers["location"] == "/notify/sidekick"


def test_auth_unknown_on_a_pi_waits(monkeypatch):
    token, _ = paired()
    monkeypatch.setattr(pebble, "session_secret", lambda: "secret")  # a Pi, but Auth never answered
    r = client.get(f"{B}/config", headers=auth(token))
    assert r.status_code == 503 and r.json()["error"] == "unavailable" and r.headers["Retry-After"] == "30"


def test_pending_codes_are_capped(monkeypatch):
    for i, code in enumerate(("ACDE", "ACDF", "ACDH", "ACDJ")):
        assert poll(code=code, device=f"pbl-00000000000{i}").status_code == 202
    r = poll(code="ACDK", device="pbl-000000000009")
    assert r.status_code == 429 and r.json()["retry_after_s"] == 60
    monkeypatch.setattr(pebble, "MAX_PENDING_PER_IP", 10)
    monkeypatch.setattr(pebble, "MAX_PENDING", 4)
    assert poll(code="ACDK", device="pbl-000000000009").status_code == 429


def test_ai_call_is_time_limited(isolated):
    token, _ = paired()
    client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    timeout = isolated[-1][1]["timeout"]
    assert timeout.read <= 25


def test_slow_ai_ends_inside_the_deadline(monkeypatch):
    """Fallback models can't push an ask past the device's wait: JSON 503, fast."""
    import stonepi_ai

    monkeypatch.setattr(ask, "ASK_DEADLINE_S", 9.0)  # budget ≈ 9 - 5 (speech reserve) = 4 s
    monkeypatch.setattr(stonepi_ai, "complete", lambda *_a, **_k: time.sleep(6) or FakeResult("late"))
    token, _ = paired()
    started = time.monotonic()
    r = client.post(f"{B}/ask", content=wav(1.0), headers={**auth(token), "Content-Type": "audio/wav"})
    assert r.status_code == 503 and r.json()["error"] == "ai_unavailable"
    assert time.monotonic() - started < 5.5


def test_unexpected_errors_are_protocol_json(monkeypatch):
    token, _ = paired()

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pebble.mood, "compute", boom)
    crashy = TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)
    r = crashy.get(f"{B}/mood", headers=auth(token))
    assert r.status_code == 500 and r.json() == {"error": "internal", "message": "Something went wrong"}


def test_lan_url_skips_loopback_and_tailscale(monkeypatch):
    monkeypatch.setattr(env, "lan_url", "")
    monkeypatch.setattr(env, "lan_port", 80)
    for bad in ("127.0.0.1", "100.101.102.103", "8.8.8.8"):
        assert not pebble._home_ip(bad)
    assert pebble._home_ip("192.168.0.223") and pebble._home_ip("10.0.0.5")
    monkeypatch.setattr(pebble, "_lan_ip", lambda: "")
    monkeypatch.setenv("STONEPI_HOSTNAME", "stonepi")
    assert pebble.lan_url() == "http://stonepi.local"
    monkeypatch.setattr(pebble, "_lan_ip", lambda: "192.168.0.223")
    assert pebble.lan_url() == "http://192.168.0.223"


def test_whisper_gets_the_vocabulary(monkeypatch):
    from app import speech

    monkeypatch.setattr(env, "whisper_hint", "")
    opts = speech.transcribe_options()
    assert "StonePi" in opts["initial_prompt"] and "NewsCast" in opts["initial_prompt"]
    assert opts["language"] == "en" and opts["vad_filter"] is True
    monkeypatch.setattr(env, "whisper_hint", "Eva, Aarhus")
    assert opts != speech.transcribe_options()
    assert speech.transcribe_options()["initial_prompt"].endswith("Tailscale, Eva, Aarhus.")


def test_warm_up_loads_both_models_only_in_auto(monkeypatch, tmp_path):
    from app import speech

    loaded = []
    monkeypatch.setattr(speech, "_whisper_model", lambda: loaded.append("whisper"))
    monkeypatch.setattr(speech, "_synthesize_python", lambda text: loaded.append("piper"))
    monkeypatch.setattr(speech, "_piper_module", lambda: True)
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    monkeypatch.setattr(env, "piper_voice", str(voice))
    speech.warm_up()  # fixture: dev mode
    assert loaded == []
    monkeypatch.setattr(env, "speech", "auto")
    speech.warm_up()
    assert loaded == ["whisper", "piper"]
    # A missing engine is logged, never raised.
    monkeypatch.setattr(speech, "_whisper_model", lambda: (_ for _ in ()).throw(speech.SpeechUnavailable("not installed")))
    speech.warm_up()


def test_startup_runs_warm_up(monkeypatch):
    from app import speech

    ran = threading.Event()
    monkeypatch.setattr(speech, "warm_up", ran.set)
    with TestClient(app, client=("127.0.0.1", 50000)) as started:
        assert started.get("/healthz").status_code == 200
    assert ran.wait(2)


def _serve_and_poll(options: dict) -> int:
    """A real uvicorn server, called the way nginx calls it: from loopback, with
    X-Forwarded-For naming the LAN device and X-StonePi-Path: lan."""
    import socket as _socket

    import httpx
    import uvicorn

    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", **options))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        r = httpx.post(
            f"http://127.0.0.1:{port}{B}/claim/poll",
            json={"code": "ACDE", "device_id": DEVICE},
            headers={"X-Forwarded-For": "192.168.0.50", "X-Real-IP": "192.168.0.50", "X-StonePi-Path": "lan"},
            timeout=5,
        )
        return r.status_code
    finally:
        server.should_exit = True
        thread.join(5)


def test_behind_nginx_lan_devices_can_pair(monkeypatch):
    """Regression (Pi, 2026-10-24): uvicorn's default proxy_headers rewrote the loopback peer to
    the LAN client, so nginx's X-StonePi-Path was ignored and pairing answered 'Pair Pebble at home'."""
    from app import serve

    monkeypatch.setattr(env, "default_path", "away")  # as on the Pi
    assert serve.OPTIONS["proxy_headers"] is False
    assert _serve_and_poll({"proxy_headers": True}) == 404  # the bug
    pebble.reset_runtime()
    opts = {k: v for k, v in serve.OPTIONS.items() if k != "access_log"}
    assert _serve_and_poll(opts) == 202  # fixed: waiting for Sidekick


def test_quiet_hours_through_sidekick():
    paired()
    path = f"{internal.PREFIX}/pebble/overview"
    overview = client.get(path, headers=sign_internal_v2(SECRET, "GET", path)).json()
    assert overview["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00"}
    assert overview["devices"][0]["mood"]["mood"] in mood.MOODS
    settings_path = f"{internal.PREFIX}/pebble/settings"
    assert signed(settings_path, {"quiet_hours": {"enabled": True, "start": "7pm", "end": "07:00"}}).status_code == 400
    assert signed(settings_path, {"quiet_hours": "off"}).status_code == 400
    assert client.post(settings_path, json={"quiet_hours": {"enabled": False}}).status_code == 404  # unsigned
    r = signed(settings_path, {"quiet_hours": {"enabled": True, "start": "23:30", "end": "06:30"}})
    assert r.status_code == 200 and r.json()["ok"]
    overview = client.get(path, headers=sign_internal_v2(SECRET, "GET", path)).json()
    assert overview["quiet_hours"] == {"enabled": True, "start": "23:30", "end": "06:30"}
