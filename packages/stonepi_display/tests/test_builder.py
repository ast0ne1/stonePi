"""Grid placement, universal template, per-Display push."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stonepi_display
from stonepi_contracts import WIDGET_CATALOG
from stonepi_display import SAMPLE_VARIABLES, push as push_mod
from stonepi_display.displays import normalize_display
from stonepi_display.grid import layout_rows, place_widgets
from stonepi_display.templates import WIDGET_SNIPPETS, liquid_references, snippet_keys


def _display(widgets, device="og", **extra):
    return normalize_display({"id": "kitchen", "name": "Kitchen", "device": device, "widgets": widgets, **extra})


# --- grid ------------------------------------------------------------------


def test_auto_places_in_reading_order():
    d = _display(
        [
            {"widget_id": "stonepi.system_status", "size": "wide"},
            {"widget_id": "stonepi.watch", "size": "small"},
            {"widget_id": "stonepi.apps", "size": "large"},
        ]
    )
    spots = [(w["widget_id"], w["x"], w["y"]) for w in d["widgets"]]
    assert spots == [("stonepi.system_status", 1, 1), ("stonepi.watch", 1, 2), ("stonepi.apps", 2, 2)]


def test_keeps_valid_positions_and_moves_overlaps():
    placed = place_widgets(
        [
            {"id": "a", "widget_id": "stonepi.watch", "size": "medium", "x": 3, "y": 2},
            {"id": "b", "widget_id": "stonepi.storage", "size": "medium", "x": 3, "y": 2},
        ],
        "og",
    )
    assert (placed[0]["x"], placed[0]["y"]) == (3, 2)
    assert (placed[1]["x"], placed[1]["y"]) == (1, 1)


def test_widgets_that_do_not_fit_are_unplaced_not_dropped():
    d = _display([{"widget_id": "stonepi.apps", "size": "large"} for _ in range(4)])
    assert len(d["widgets"]) == 4
    assert [bool(w["x"]) for w in d["widgets"]] == [True, True, False, False]
    assert len(layout_rows(d)) == 2


def test_size_falls_back_to_an_allowed_one():
    d = _display([{"widget_id": "stonepi.apps", "size": "small"}])
    assert d["widgets"][0]["size"] == "medium"


def test_layout_rows_are_compact_and_sorted():
    d = _display(
        [
            {"widget_id": "stonepi.storage", "size": "small", "x": 4, "y": 3},
            {"widget_id": "stonepi.system_status", "size": "wide", "x": 1, "y": 1},
        ]
    )
    assert layout_rows(d) == [["sys", 1, 1, 4, 1], ["sto", 4, 3, 1, 1]]


def test_wide_spans_the_device_width():
    assert layout_rows(_display([{"widget_id": "stonepi.system_status", "size": "wide"}], device="v2"))[0][3] == 4


# --- template --------------------------------------------------------------


def test_every_catalog_widget_has_a_snippet_and_code():
    for desc in WIDGET_CATALOG:
        assert desc.code, desc.id
        assert desc.code in WIDGET_SNIPPETS, desc.id


@pytest.mark.parametrize("code", sorted(WIDGET_SNIPPETS))
def test_snippet_reads_only_contract_variables(code):
    paths, loops = liquid_references(WIDGET_SNIPPETS[code])
    for path in paths:
        head, *rest = path.split(".")
        if head in {"w", "cw", "ch"}:
            continue
        value = (SAMPLE_VARIABLES.get(loops[head]) or [None])[0] if head in loops else SAMPLE_VARIABLES.get(head)
        assert value is not None or head in SAMPLE_VARIABLES, f"{code}: {path}"
        for part in rest:
            assert isinstance(value, dict) and part in value, f"{code}: {path}"
            value = value[part]


@pytest.mark.parametrize("device", ["og", "v2"])
@pytest.mark.parametrize("size", ["small", "medium", "large", "wide"])
def test_every_widget_renders_at_every_size(device, size):
    pytest.importorskip("liquid")
    for desc in WIDGET_CATALOG:
        if size not in desc.sizes:
            continue
        d = _display([{"widget_id": desc.id, "size": size}], device=device)
        html = stonepi_display.render_markup(stonepi_display.display_payload(d, SAMPLE_VARIABLES))
        assert "grid-column:1 / span" in html, desc.id
        assert "{{" not in html and "{%" not in html


def test_empty_display_renders_a_hint():
    pytest.importorskip("liquid")
    html = stonepi_display.render_markup(stonepi_display.display_payload(_display([]), SAMPLE_VARIABLES))
    assert "No widgets yet" in html


def test_preview_document_uses_the_trmnl_skeleton():
    pytest.importorskip("liquid")
    doc = stonepi_display.preview_document(stonepi_display.display_payload(_display([]), SAMPLE_VARIABLES), device="v2")
    assert '<body class="environment trmnl">' in doc
    assert "screen--v2" in doc and 'class="view view--full"' in doc


# --- payload ---------------------------------------------------------------


def test_payload_carries_only_what_the_widgets_read():
    d = _display([{"widget_id": "newscast.headlines", "size": "small"}])
    payload = stonepi_display.display_payload(d, SAMPLE_VARIABLES)
    assert set(payload) == {"L", "G", "TB", "TN", "updated_at"} | snippet_keys("nws")
    assert payload["G"] == [4, 3]


def test_legacy_template_sends_every_variable():
    d = _display([], trmnl={"template": "legacy"})
    payload = stonepi_display.display_payload(d, SAMPLE_VARIABLES)
    assert set(SAMPLE_VARIABLES) <= set(payload)


def test_fit_payload_trims_lists_but_never_the_layout():
    big = {"L": [["app", 1, 1, 2, 2]] * 3, "G": [4, 3], "apps": [{"n": "x" * 60}] * 40, "hostname": "stonepi"}
    fitted = push_mod.fit_payload(big, limit=600)
    assert len(json.dumps(fitted, separators=(",", ":")).encode()) <= 600
    assert fitted["L"] == big["L"] and fitted["G"] == [4, 3]
    assert 0 < len(fitted["apps"]) < 40


def test_webhook_keys():
    assert push_mod.webhook_key("dashboard") == "DISPLAY_WEBHOOK_URL"
    assert push_mod.webhook_key("a1b2-c3") == "DISPLAY_WEBHOOK_URL_A1B2_C3"


# --- push ------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    sent: list = []
    status = 200

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        _FakeClient.sent.append((url, json))
        return _FakeResponse(_FakeClient.status)


@pytest.fixture
def pushing(tmp_path: Path, monkeypatch):
    stonepi_display.configure_displays(tmp_path)
    hooks = {"kitchen": "https://trmnl.example/api/custom_plugins/abc"}
    monkeypatch.setattr(push_mod, "get_webhook", lambda display_id: hooks.get(display_id, ""))
    monkeypatch.setattr(push_mod, "validate_webhook_url", lambda url: url)
    monkeypatch.setattr(push_mod.httpx, "Client", _FakeClient)
    _FakeClient.sent = []
    _FakeClient.status = 200
    stonepi_display.upsert_display(
        {
            "id": "kitchen",
            "name": "Kitchen",
            "device": "og",
            "widgets": [{"widget_id": "stonepi.watch", "size": "small"}],
            "trmnl": {"enabled": True, "interval_minutes": 15},
        }
    )
    return hooks


def test_push_posts_merge_variables_and_records_status(pushing):
    result = stonepi_display.push_display("kitchen", SAMPLE_VARIABLES)
    assert result["ok"] is True
    url, body = _FakeClient.sent[0]
    assert url.endswith("/abc")
    assert body["merge_variables"]["L"] == [["wat", 1, 1, 1, 1]]
    trmnl = stonepi_display.get_display("kitchen")["trmnl"]
    assert trmnl["last_push_ok"] is True and trmnl["last_push_at"]


def test_push_reports_rate_limit(pushing):
    _FakeClient.status = 429
    result = stonepi_display.push_display("kitchen", SAMPLE_VARIABLES)
    assert result["ok"] is False and "rate limit" in result["message"]


def test_push_due_respects_interval_and_collects_once(pushing):
    calls = []

    def collect():
        calls.append(1)
        return SAMPLE_VARIABLES

    first = stonepi_display.push_due(collect)
    second = stonepi_display.push_due(collect)
    forced = stonepi_display.push_due(collect, force=True)
    assert [r["display_id"] for r in first] == ["kitchen"]
    assert second == []
    assert len(forced) == 1
    assert len(calls) == 2  # nothing due on the second tick → no collection


def test_display_without_webhook_is_not_pushed(pushing):
    stonepi_display.upsert_display({"id": "hall", "name": "Hall", "trmnl": {"enabled": True}, "widgets": []})
    assert [d["id"] for d in stonepi_display.pushable_displays()] == ["kitchen"]


# --- title bar -------------------------------------------------------------


@pytest.mark.parametrize("position", ["top", "bottom", "off"])
def test_title_bar_position(position):
    pytest.importorskip("liquid")
    d = _display([{"widget_id": "stonepi.watch", "size": "small"}], title_bar=position)
    payload = stonepi_display.display_payload(d, SAMPLE_VARIABLES)
    assert payload["TB"] == position
    html = stonepi_display.render_markup(payload)
    bar, grid = html.find('class="title_bar"'), html.find('class="layout"')
    if position == "off":
        assert bar == -1 and "TN" not in payload and "updated_at" not in payload
    else:
        assert (bar < grid) == (position == "top")
        assert "Kitchen" in html  # the Display's name, not a fixed "StonePi"


def test_unknown_title_bar_and_date_format_fall_back():
    d = _display([], title_bar="sideways", date_format="nope")
    assert d["title_bar"] == "bottom" and d["date_format"] == "day_time"


def test_date_formats_render_in_local_time():
    from datetime import datetime, timezone

    stamp = datetime(2026, 9, 27, 13, 30, tzinfo=timezone.utc)
    local = stamp.astimezone()
    assert stonepi_display.format_updated(stamp.isoformat(), "iso") == local.strftime("%Y-%m-%d %H:%M")
    assert stonepi_display.format_updated(stamp.isoformat(), "time") == local.strftime("%H:%M")
    assert stonepi_display.format_updated(stamp.isoformat(), "mdy").endswith(("AM", "PM"))
    d = _display([], date_format="dmy")
    payload = stonepi_display.display_payload(d, {**SAMPLE_VARIABLES, "updated_ts": stamp.isoformat()})
    assert payload["updated_at"] == local.strftime("%d/%m/%Y %H:%M")
