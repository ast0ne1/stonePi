"""The merge_variables builder is the one contract every widget snippet reads."""

from __future__ import annotations

import json

from stonepi_display import MERGE_VARIABLE_KEYS, SAMPLE_VARIABLES, build_merge_variables


def test_builder_emits_the_same_keys_for_empty_inputs():
    empty = build_merge_variables(hostname="", watch={}, backup={}, fetched={})
    assert set(empty) == MERGE_VARIABLE_KEYS


def test_builder_maps_app_display_payloads():
    variables = build_merge_variables(
        hostname="pi",
        watch={"level": "healthy", "summary": "All clear", "apps": [{"id": "fileserve", "n": "FileServe", "running": True}]},
        backup={"status": "failed"},
        fetched={"newscast": {"feeds": 4, "updated": "07:00"}, "fileserve": {"pages": 2}, "pinboard": {"lines": ["a", "b"], "total": 5}},
        cpu=9,
    )
    assert variables["system_ok"] is True
    assert variables["cpu_disp"] == "9%" and variables["mem_disp"] == "n/a"
    assert variables["nc_feeds"] == 4 and variables["fs_pages"] == 2 and variables["fs_ok"] is True
    assert variables["pinboard_more"] == 3
    assert variables["backup_ok"] is False


def test_sample_fits_trmnl_standard_payload():
    raw = json.dumps(SAMPLE_VARIABLES, separators=(",", ":"), default=str)
    assert len(raw.encode("utf-8")) <= 2048


def test_watch_summary_keeps_names_of_services_not_running():
    watch = {"level": "attention", "summary": "Multiple services are not running",
             "summary_detail": "NewsCast, Studio", "reasons": [], "apps": []}
    variables = build_merge_variables(hostname="pi", watch=watch, backup={}, fetched={})
    assert variables["watch_summary"] == "Multiple services are not running (NewsCast, Studio)"
    # Already joined (Dashboard status API): not doubled.
    joined = dict(watch, summary="Multiple services are not running (NewsCast, Studio)")
    assert build_merge_variables(hostname="pi", watch=joined, backup={}, fetched={})["watch_summary"] == joined["summary"]
