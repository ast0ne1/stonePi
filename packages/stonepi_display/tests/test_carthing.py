from __future__ import annotations

from pathlib import Path

import pytest

from stonepi_display import carthing


@pytest.fixture()
def store(tmp_path: Path):
    carthing.configure_carthing(tmp_path)
    return tmp_path


def test_default_config_is_already_normal():
    cfg = carthing.default_config()
    assert carthing.normalize_config(cfg) == cfg


def test_normalize_drops_unknown_and_disallowed():
    cfg = carthing.normalize_config(
        {
            "id": "Kitchen Panel!",
            "home": {"widgets": ["newscast", "pricewatch", "newscast", "system"]},
            "allowed_actions": ["open:newscast", "select", "bogus"],
            "controls": {"preset1": "open:newscast", "preset2": "open:sportguide", "dial_press": "select", "nope": "home"},
            "clock": {"face": "nixie", "color": "#abcdef", "accent": "red", "seconds": True},
            "idle": {"screensaver_after_s": 1, "quiet_hours": {"from": "25:00", "mode": "off"}},
        }
    )
    assert cfg["id"] == "kitchen-panel"
    # A single Home (schema 1) becomes page 1, small cards
    assert cfg["pages"] == [{"name": "Home", "widgets": [{"id": "newscast", "size": "small"}, {"id": "system", "size": "small"}]}]
    # home + back are always allowed; page actions didn't exist in schema 1, so they're allowed too
    assert set(cfg["allowed_actions"]) == {"home", "back", "select", "open:newscast", *carthing.PAGE_ACTION_IDS}
    assert cfg["controls"]["preset1"] == "open:newscast"
    assert cfg["controls"]["preset2"] is None  # not allowed → unmapped
    assert "nope" not in cfg["controls"]
    assert cfg["clock"]["face"] == "digital_bold"
    assert cfg["clock"]["color"] == "#ABCDEF"
    assert cfg["clock"]["accent"] == "#FF7A1A"
    assert cfg["clock"]["seconds"] is True
    assert cfg["idle"]["screensaver_after_s"] == 10
    assert cfg["idle"]["quiet_hours"]["from"] == "22:30"
    assert cfg["idle"]["quiet_hours"]["mode"] == "off"


def test_image_background_needs_asset_id():
    cfg = carthing.normalize_config({"idle": {"background": {"type": "image", "value": "../etc/passwd"}}})
    assert cfg["idle"]["background"] == {"type": "gradient", "value": "midnight"}
    cfg = carthing.normalize_config({"idle": {"background": {"type": "image", "value": "0123456789abcdef"}}})
    assert cfg["idle"]["background"]["type"] == "image"


def test_weather_needs_both_coordinates():
    assert carthing.normalize_config({"weather": {"lat": 55.7}})["weather"]["lat"] is None
    w = carthing.normalize_config({"weather": {"lat": "55.6761", "lon": 12.5683, "label": "Copenhagen"}})["weather"]
    assert (w["lat"], w["lon"], w["label"]) == (55.6761, 12.5683, "Copenhagen")


def test_store_seeds_default_and_snapshots(store: Path):
    rows = carthing.list_configs()
    assert [r["id"] for r in rows] == ["default"] and rows[0]["active"]
    cfg = carthing.active_config()
    for face in ("segment", "words", "analogue"):
        cfg["clock"]["face"] = face
        carthing.save_config(cfg)
    snaps = carthing.list_snapshots("default")
    assert len(snaps) == 3
    restored = carthing.restore_snapshot("default", snaps[-1]["id"])
    assert restored["clock"]["face"] == "digital_bold"


def test_snapshots_are_capped(store: Path):
    cfg = carthing.active_config()
    for _ in range(carthing.MAX_SNAPSHOTS + 4):
        carthing.save_config(cfg)
    assert len(carthing.list_snapshots("default")) == carthing.MAX_SNAPSHOTS


def test_copy_activate_delete(store: Path):
    carthing.list_configs()
    clone = carthing.copy_config("default", "Night")
    assert clone["id"] == "night"
    assert carthing.copy_config("default", "Night")["id"] == "night-2"
    carthing.activate_config("night")
    assert carthing.active_config()["id"] == "night"
    with pytest.raises(ValueError):
        carthing.delete_config("night")
    carthing.delete_config("default")
    assert {r["id"] for r in carthing.list_configs()} == {"night", "night-2"}


def test_import_never_overwrites(store: Path):
    carthing.list_configs()
    exported = carthing.export_config("default")
    imported = carthing.import_config(exported)
    assert imported["id"] != "default"
    with pytest.raises(ValueError):
        carthing.import_config(["not", "a", "config"])


def test_token_pairing(store: Path):
    token = carthing.new_pairing_token()
    device = carthing.load_device()
    assert carthing.token_ok(token, device["token_hash"])
    assert not carthing.token_ok("wrong", device["token_hash"])
    newer = carthing.new_pairing_token()
    assert not carthing.token_ok(token, carthing.load_device()["token_hash"])
    assert carthing.token_ok(newer, carthing.load_device()["token_hash"])


def test_pin(store: Path):
    with pytest.raises(ValueError):
        carthing.set_pin(None, enabled=True)  # nothing set yet
    with pytest.raises(ValueError):
        carthing.set_pin("12a4", enabled=True)
    device = carthing.set_pin("4821", enabled=True)
    assert device["pin"]["enabled"]
    assert carthing.pin_ok("4821", device["pin"])
    assert not carthing.pin_ok("4822", device["pin"])
    off = carthing.set_pin(None, enabled=False)
    assert not off["pin"]["enabled"] and off["pin"]["hash"]  # PIN kept, just switched off


def test_public_state_rev_changes_with_config(store: Path):
    first = carthing.public_state()
    assert first["config"]["id"] == "default" and "salt" in first["pin"]
    cfg = carthing.active_config()
    cfg["clock"]["color"] = "#FF4D4D"
    carthing.save_config(cfg)
    assert carthing.public_state()["rev"] != first["rev"]


# ── Pages (schema 2) ─────────────────────────────────────────────────────────

SCHEMA_1 = {
    "id": "kitchen",
    "name": "Kitchen",
    "updated": "2026-10-02T10:00:00+00:00",
    "home": {"widgets": ["pinboard", "system"]},
    "allowed_actions": ["home", "back", "next", "prev", "select", "open:pinboard"],
    "controls": {"dial_cw": "next", "dial_ccw": "prev", "dial_press": "select", "back": "back"},
    "idle": {"screensaver_after_s": 120},
}


def test_schema_1_file_loads_as_page_one(store: Path):
    import json

    folder = store / "carthing" / "configs"
    folder.mkdir(parents=True)
    (folder / "kitchen.json").write_text(json.dumps(SCHEMA_1), encoding="utf-8")
    cfg = carthing.get_config("kitchen")
    assert cfg["schema"] == carthing.CONFIG_SCHEMA
    assert cfg["pages"] == [{"name": "Home", "widgets": [{"id": "pinboard", "size": "small"}, {"id": "system", "size": "small"}]}]
    assert cfg["rotation"] == {"every_s": 0, "resume_after_s": 30}
    assert "next_page" in cfg["allowed_actions"] and "page:2" in cfg["allowed_actions"]
    assert "open:newscast" not in cfg["allowed_actions"]  # the rest of the old allow-list is kept as it was
    assert cfg["controls"]["dial_cw"] == "next" and cfg["idle"]["screensaver_after_s"] == 120
    assert "home" not in cfg
    # Reading never rewrites the file; saving does, as schema 2.
    assert "home" in json.loads((folder / "kitchen.json").read_text(encoding="utf-8"))
    carthing.save_config(cfg)
    assert json.loads((folder / "kitchen.json").read_text(encoding="utf-8"))["schema"] == 2


def test_old_export_imports_and_new_export_is_marked(store: Path):
    carthing.list_configs()
    imported = carthing.import_config({"stonepi_carthing_config": 1, **SCHEMA_1})
    assert imported["id"] == "kitchen" and [w["id"] for w in imported["pages"][0]["widgets"]] == ["pinboard", "system"]
    exported = carthing.export_config("kitchen")
    assert exported["stonepi_carthing_config"] == 2 and exported["pages"] == imported["pages"]
    assert carthing.import_config(exported)["pages"] == imported["pages"]


def test_schema_2_allow_list_is_respected():
    cfg = carthing.normalize_config({"pages": [{"name": "A", "widgets": []}], "allowed_actions": ["select"]})
    assert set(cfg["allowed_actions"]) == {"home", "back", "select"}
    cfg = carthing.normalize_config(
        {"pages": [], "allowed_actions": ["next_page", "page:2"], "controls": {"swipe_left": "next_page", "preset1": "page:2", "preset2": "page:9"}}
    )
    assert cfg["controls"]["swipe_left"] == "next_page" and cfg["controls"]["preset1"] == "page:2"
    assert cfg["controls"]["preset2"] is None
    assert cfg["pages"] == [{"name": "Home", "widgets": []}]  # never zero pages


def test_pages_keep_what_fits():
    cfg = carthing.normalize_config(
        {
            "pages": [
                {"name": "  Mixed  ", "widgets": [
                    {"id": "system", "size": "half"},
                    "newscast",
                    {"id": "clock", "size": "small"},
                    {"id": "pinboard", "size": "half"},  # 3 + 1 + 1 + 3 > 6: dropped
                    {"id": "eventtrakr", "size": "huge"},  # unknown size → small, fits (6)
                    {"id": "sportguide"},  # page full
                    {"id": "newscast", "size": "small"},  # repeat
                    {"id": "pricewatch", "size": "small"},  # not a widget
                ]},
                {"widgets": [{"id": "clock", "size": "full"}, {"id": "system", "size": "small"}]},
                "not a page",
            ]
            + [{"name": f"P{n}", "widgets": []} for n in range(10)],
            "rotation": {"every_s": 2, "resume_after_s": 99999},
        }
    )
    first, second = cfg["pages"][0], cfg["pages"][1]
    assert first["name"] == "Mixed"
    assert [(w["id"], w["size"]) for w in first["widgets"]] == [
        ("system", "half"), ("newscast", "small"), ("clock", "small"), ("eventtrakr", "small")
    ]
    assert second == {"name": "Page 2", "widgets": [{"id": "clock", "size": "full"}]}
    assert len(cfg["pages"]) == carthing.MAX_PAGES
    assert cfg["rotation"] == {"every_s": 5, "resume_after_s": 3600}
    assert carthing.normalize_config({"rotation": {"every_s": 0}})["rotation"]["every_s"] == 0


def test_screensaver_can_be_never():
    assert carthing.normalize_config({"idle": {"screensaver_after_s": 0}})["idle"]["screensaver_after_s"] == 0
    assert carthing.normalize_config({"idle": {"screensaver_after_s": 3}})["idle"]["screensaver_after_s"] == 10


def test_page_layout():
    small = lambda i: {"id": i, "size": "small"}  # noqa: E731
    half = lambda i: {"id": i, "size": "half"}  # noqa: E731
    assert carthing.page_layout([small("a"), small("b")]) == {"kind": "grid", "cols": [[small("a"), small("b")]]}
    assert carthing.page_layout([{"id": "c", "size": "full"}]) == {"kind": "full", "cols": [[{"id": "c", "size": "full"}]]}
    # A half sits on the side where it comes first; small ones stack in the other half.
    assert carthing.page_layout([half("s"), small("a"), small("b")])["cols"] == [[half("s")], [small("a"), small("b")]]
    assert carthing.page_layout([small("a"), half("s"), small("b")])["cols"] == [[small("a"), small("b")], [half("s")]]
    assert carthing.page_layout([half("x"), half("y")]) == {"kind": "split", "cols": [[half("x")], [half("y")]]}


def test_page_widget_ids_in_page_order():
    cfg = carthing.normalize_config(
        {"pages": [{"widgets": ["newscast", "clock"]}, {"widgets": ["system", {"id": "newscast", "size": "half"}]}]}
    )
    assert carthing.page_widget_ids(cfg) == ["newscast", "clock", "system"]
