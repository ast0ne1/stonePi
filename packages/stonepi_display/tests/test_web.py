"""F14: web pages on the displays (store, e-ink image, TRMNL Web page Displays, Car Thing schema)."""

from __future__ import annotations

import io
import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import stonepi_display
from stonepi_display import carthing, webcapture, webimage, webpages
from stonepi_display import push as push_mod
from stonepi_display.displays import BUILTIN_DASHBOARD_ID, get_display, normalize_display, upsert_display


def _png(width: int, height: int, *, dark: bool = False) -> bytes:
    img = Image.new("RGB", (width, height), (15, 18, 25) if dark else (245, 245, 245))
    ImageDraw.Draw(img).rectangle((20, 20, width // 2, height // 2), fill=(240, 240, 240) if dark else (10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ── Web pages store ──────────────────────────────────────────────────────────


def test_pages_round_trip_and_validation(tmp_path: Path):
    stonepi_display.configure_displays(tmp_path)
    page = stonepi_display.upsert_page({"id": "homepage", "name": "Homepage", "url": "http://192.168.0.10:3000/"})
    assert page["preset"] == "flat" and page["settle_s"] == 3 and page["scale"] == 1.0
    assert stonepi_display.get_page("homepage")["url"] == "http://192.168.0.10:3000/"
    assert stonepi_display.new_page_id("Homepage") == "homepage-2"
    for bad in ("ftp://x/", "homepage", "http://user:pw@host/"):
        with pytest.raises(ValueError):
            stonepi_display.upsert_page({"id": "x", "name": "X", "url": bad})
    clamped = stonepi_display.upsert_page(
        {"id": "homepage", "name": "H", "url": "https://h.local/", "settle_s": 99, "scale": 0.01, "preset": "nope"}
    )
    assert (clamped["settle_s"], clamped["scale"], clamped["preset"]) == (15, 0.25, "flat")
    assert stonepi_display.delete_page("homepage") and stonepi_display.load_pages() == []


def test_page_css_one_rule_per_hidden_selector():
    css = webpages.page_css({"preset": "none", "hide": "#top\n\n.search\nbad{x}", "css": "body{color:red}"})
    assert "#top{display:none!important}" in css and ".search{display:none!important}" in css
    assert "bad{x}" not in css.split("body")[0] and css.endswith("body{color:red}")


# ── e-ink conversion ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bits", [1, 2, 4])
@pytest.mark.parametrize("width", [1, 7, 37, 800])
def test_grey_png_packs_pixels_exactly(bits: int, width: int):
    top = (1 << bits) - 1
    values = [random.randint(0, top) for _ in range(width * 3)]
    levels = Image.new("L", (width, 3))
    levels.putdata(values)
    png, _ = webimage._png_grey(levels, bits)
    back = Image.open(io.BytesIO(png))
    assert back.size == (width, 3)
    assert [round(v * top / 255) for v in back.convert("L").getdata()] == values


def test_to_eink_sizes_inverts_dark_pages_and_hashes_pixels():
    out, info = webimage.to_eink(_png(800, 480, dark=True), 800, 480, bits=1)
    img = Image.open(io.BytesIO(out))
    assert img.size == (800, 480) and img.mode == "1"
    assert info["inverted"] and img.convert("L").getpixel((700, 400)) == 255  # dark background → white
    light, light_info = webimage.to_eink(_png(800, 480), 800, 480, bits=1)
    assert not light_info["inverted"]
    _, again = webimage.to_eink(_png(800, 480), 800, 480, bits=1)
    assert again["hash"] == light_info["hash"]
    _, never = webimage.to_eink(_png(800, 480, dark=True), 800, 480, bits=1, invert="off")
    assert not never["inverted"]
    # A capture a pixel out (zoom rounding) is padded to the exact panel size.
    four, _ = webimage.to_eink(_png(1871, 1403), 1872, 1404, bits=4, dither=True)
    assert Image.open(io.BytesIO(four)).size == (1872, 1404)


# ── TRMNL Web page Displays ──────────────────────────────────────────────────


def test_web_display_normalisation(tmp_path: Path):
    stonepi_display.configure_displays(tmp_path)
    web = normalize_display(
        {"id": "w1", "name": "Home", "kind": "web", "widgets": [{"widget_id": "stonepi.apps"}],
         "web": {"page_id": "homepage", "bits": 3, "invert": "sideways", "threshold": 999},
         "trmnl": {"interval_minutes": 1, "tier": "standard"}}
    )
    assert web["kind"] == "web" and web["widgets"] == []
    assert web["web"] == {"page_id": "homepage", "bits": 0, "dither": False, "invert": "auto", "threshold": 250}
    assert web["trmnl"]["interval_minutes"] == 5  # Webhook Image floor, not Standard's 10
    assert stonepi_display.push_limits(web)["per_hour"] == 12
    assert stonepi_display.web_bits(web) == 1 and stonepi_display.web_bits({**web, "device": "v2"}) == 4
    builtin = normalize_display({"id": BUILTIN_DASHBOARD_ID, "kind": "web"})
    assert builtin["kind"] == "widgets"


class _FakeClient:
    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        _FakeClient.posts.append({"url": url, **kwargs})

        class _Response:
            status_code = 200
            text = ""
            headers: dict = {}

        return _Response()


@pytest.fixture
def web_display(tmp_path: Path, monkeypatch):
    stonepi_display.configure_displays(tmp_path)
    stonepi_display.upsert_page({"id": "homepage", "name": "Homepage", "url": "http://192.168.0.10:3000/"})
    upsert_display({"id": "w1", "name": "Home", "kind": "web", "web": {"page_id": "homepage"},
                    "trmnl": {"enabled": True, "interval_minutes": 15}})
    captures: list[tuple] = []

    def fake_capture(page, width, height, *, device_scale=1.0, max_age=0.0):
        captures.append((page["id"], width, height, device_scale))
        return _png(width, height, dark=True)

    monkeypatch.setattr(webcapture, "capture", fake_capture)
    monkeypatch.setattr(push_mod, "get_webhook", lambda display_id: "https://trmnl.com/api/plugin/x")
    monkeypatch.setattr(push_mod, "validate_webhook_url", lambda url: url)
    monkeypatch.setattr(push_mod.httpx, "Client", _FakeClient)
    _FakeClient.posts = []
    return captures


def test_web_push_posts_png_and_skips_unchanged(web_display):
    result = stonepi_display.push_display("w1", {})
    assert result["ok"] and result["bytes"] > 0
    post = _FakeClient.posts[-1]
    assert post["headers"] == {"Content-Type": "image/png"} and post["content"].startswith(b"\x89PNG")
    assert "json" not in post and web_display[-1] == ("homepage", 800, 480, 1.0)
    # The scheduler leaves an unchanged image alone (nothing sent, nothing counted).
    skipped = stonepi_display.push_display("w1", {}, skip_unchanged=True)
    assert skipped["skipped"] and len(_FakeClient.posts) == 1
    assert len(get_display("w1")["trmnl"]["push_log"]) == 1


def test_web_push_x_size_and_missing_page(web_display):
    display = get_display("w1")
    upsert_display({**display, "device": "v2"})
    stonepi_display.push_display("w1", {})
    assert web_display[-1] == ("homepage", 1872, 1404, 1.8)
    assert Image.open(io.BytesIO(_FakeClient.posts[-1]["content"])).size == (1872, 1404)
    upsert_display({**get_display("w1"), "web": {"page_id": "gone"}})
    result = stonepi_display.push_display("w1", {})
    assert not result["ok"] and "Pick a web page" in result["message"]


def test_push_due_skips_collecting_for_web_only(web_display):
    def collect():
        raise AssertionError("web Displays don't collect merge_variables")

    results = stonepi_display.push_due(collect, force=True)
    assert [r["display_id"] for r in results] == ["w1"] and results[0]["ok"]


# ── Car Thing ────────────────────────────────────────────────────────────────


def test_carthing_web_widget_is_full_page_only():
    cfg = carthing.normalize_config(
        {"pages": [{"name": "Home", "widgets": [{"id": "web:homepage", "size": "small"}, {"id": "clock"}]},
                   {"name": "Bad", "widgets": [{"id": "web:Bad Id!"}]}]}
    )
    assert cfg["pages"][0]["widgets"] == [{"id": "web:homepage", "size": "full"}]  # nothing fits beside it
    assert cfg["pages"][1]["widgets"] == []
    assert carthing.web_widget_page("web:homepage") == "homepage" and carthing.web_widget_page("clock") == ""


def test_carthing_web_screensaver_options():
    saver = carthing.normalize_config(
        {"idle": {"saver": {"style": "web", "web": {"page_id": "homepage", "refresh_min": 30, "time": 1}}}}
    )["idle"]["saver"]
    assert saver["style"] == "web" and saver["web"] == {"page_id": "homepage", "refresh_min": 5, "time": True}
    bad = carthing.normalize_config({"idle": {"saver": {"web": {"page_id": "../x", "refresh_min": "x"}}}})
    assert bad["idle"]["saver"]["web"] == {"page_id": "", "refresh_min": 2, "time": False}


def test_refused_push_names_the_plugin(web_display, monkeypatch):
    class _Refused(_FakeClient):
        def post(self, url, **kwargs):
            class _Response:
                status_code = 400
                text = "bad request"
                headers: dict = {}

            return _Response()

    monkeypatch.setattr(push_mod.httpx, "Client", _Refused)
    result = stonepi_display.push_display("w1", {})
    assert not result["ok"] and "needs a Webhook Image webhook" in result["message"] and "Private Plugin" in result["message"]


# ── Review fixes ─────────────────────────────────────────────────────────────


def test_failed_capture_stamps_without_using_the_budget(web_display, monkeypatch):
    def broken(page, width, height, *, device_scale=1.0, max_age=0.0):
        raise RuntimeError("page down")

    monkeypatch.setattr(webcapture, "capture", broken)
    result = stonepi_display.push_display("w1", {})
    assert not result["ok"] and "Couldn't capture the page: page down" in result["message"]
    trmnl = get_display("w1")["trmnl"]
    assert trmnl["last_push_ok"] is False and not trmnl.get("push_log") and not _FakeClient.posts


def test_image_over_1mb_is_not_sent(web_display, monkeypatch):
    monkeypatch.setattr(push_mod, "WEBHOOK_IMAGE_MAX_BYTES", 10)
    result = stonepi_display.push_display("w1", {})
    assert not result["ok"] and "over TRMNL's 1 MB" in result["message"] and not _FakeClient.posts


def test_413_says_too_big_not_wrong_plugin(web_display, monkeypatch):
    class _TooBig(_FakeClient):
        def post(self, url, **kwargs):
            class _Response:
                status_code = 413
                text = ""
                headers: dict = {}

            return _Response()

    monkeypatch.setattr(push_mod.httpx, "Client", _TooBig)
    result = stonepi_display.push_display("w1", {})
    assert "too big" in result["message"] and "Webhook Image webhook" not in result["message"]


def test_second_push_while_one_runs_is_skipped(web_display, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow(page, width, height, *, device_scale=1.0, max_age=0.0):
        started.set()
        release.wait(5)
        return _png(width, height)

    monkeypatch.setattr(webcapture, "capture", slow)
    first: dict = {}
    worker = threading.Thread(target=lambda: first.update(stonepi_display.push_display("w1", {})))
    worker.start()
    assert started.wait(5)
    second = stonepi_display.push_display("w1", {})
    release.set()
    worker.join(5)
    assert second["skipped"] and "already running" in second["message"]
    assert first["ok"] and len(_FakeClient.posts) == 1
    assert len(get_display("w1")["trmnl"]["push_log"]) == 1


def test_capture_cache_shares_runs_and_drops_idle_locks(monkeypatch):
    import stonepi_browser

    calls: list[str] = []
    monkeypatch.setattr(stonepi_browser, "capture_page", lambda url, w, h, **kw: calls.append(url) or b"png")
    webcapture.clear()
    page = {"id": "p", "url": "http://x/", "preset": "none"}
    assert webcapture.capture(page, 10, 10, max_age=60) == b"png"
    assert webcapture.capture(page, 10, 10, max_age=60) == b"png"
    assert calls == ["http://x/"] and webcapture._key_locks == {}
    for i in range(webcapture.MAX_ENTRIES + 3):
        webcapture.capture(page, 10 + i, 10)
    assert len(webcapture._cache) == webcapture.MAX_ENTRIES and webcapture._key_locks == {}


def test_create_page_picks_a_free_id(tmp_path: Path):
    stonepi_display.configure_displays(tmp_path)
    first = stonepi_display.create_page({"name": "Homepage", "url": "http://a/"})
    second = stonepi_display.create_page({"name": "Homepage", "url": "http://b/"})
    assert (first["id"], second["id"]) == ("homepage", "homepage-2") and len(stonepi_display.load_pages()) == 2


# ── Captured pages can't reach this Pi's loopback services ──────────────────


DNS = {"127.0.0.1.nip.io": ["127.0.0.1"], "cdn.example.com": ["93.184.215.14"], "v6.test": ["::1"]}


@pytest.mark.parametrize(
    "page_url, request_url, blocked",
    [
        ("https://example.com/", "http://127.0.0.1:8012/api/events", True),
        ("https://example.com/", "http://localhost:8012/api/push-trmnl", True),
        ("https://example.com/", "http://[::1]:8003/", True),
        ("https://example.com/", "http://[::ffff:127.0.0.1]:8012/", True),
        ("https://example.com/", "http://0.0.0.0:8012/", True),
        ("https://example.com/", "http://127.0.0.1.nip.io:8012/api/events", True),  # a name for 127.0.0.1
        ("https://example.com/", "http://v6.test:8012/", True),
        ("http://127.0.0.1:3000/", "http://127.0.0.1:3000/api/widgets", False),
        ("http://127.0.0.1:3000/", "http://127.0.0.1:8012/api/events", True),
        ("http://192.168.0.10:3000/", "http://192.168.0.10:3000/api/x", False),
        ("https://example.com/", "https://cdn.example.com/app.js", False),
        ("https://example.com/", "https://unknown.example/app.js", False),
        ("https://example.com/", "data:image/png;base64,xx", False),
    ],
)
def test_capture_blocks_loopback_requests(page_url, request_url, blocked):
    from stonepi_browser.capture import blocked_request

    assert blocked_request(page_url, request_url, lambda host: DNS.get(host, [])) is blocked


def test_capture_url_logged_without_query():
    from stonepi_browser.capture import _safe_url

    assert _safe_url("http://ha.local:8123/lovelace?token=secret#x") == "http://ha.local:8123/lovelace"


def test_chromium_lock_times_out(tmp_path: Path, monkeypatch):
    import threading

    from stonepi_browser import ChromiumBusy, chromium_lock

    monkeypatch.setenv("STONEPI_CHROMIUM_LOCK", str(tmp_path / "chromium.lock"))
    held, release = threading.Event(), threading.Event()

    def holder():
        with chromium_lock():
            held.set()
            release.wait(5)

    worker = threading.Thread(target=holder)
    worker.start()
    assert held.wait(5)
    try:
        with pytest.raises(ChromiumBusy):
            with chromium_lock(timeout=0.2):
                pass
    finally:
        release.set()
        worker.join(5)
    with chromium_lock(timeout=1):
        pass
