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


def test_cpu_is_measured_between_calls(monkeypatch, tmp_path):
    """CPU % covers the time since the last reading, not a fresh 0.12 s blip per call."""
    from types import SimpleNamespace

    from stonepi_display import variables

    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4\n", encoding="utf-8")
    assert variables._proc_stat_ticks(str(stat)) == (1000, 850)

    clock = {"now": 1000.0}
    samples = iter([(1000, 850), (1100, 940), (2100, 1440), (2101, 1440), (2201, 1540)])
    sleeps: list[float] = []
    monkeypatch.setattr(variables, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(variables, "_proc_stat_ticks", lambda path="/proc/stat": next(samples))
    monkeypatch.setattr(variables.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(variables.time, "sleep", lambda s: sleeps.append(s) or clock.update(now=clock["now"] + s))
    monkeypatch.setattr(variables, "_cpu_state", {"ticks": None, "at": 0.0, "value": None})

    # First call in a process: a short sample (100 jiffies, 90 idle) → 10 %.
    assert variables.read_cpu_pct() == 10 and sleeps == [variables._CPU_SAMPLE_S]
    # 30 s later: everything since the last reading (1000 jiffies, 500 idle) → 50 %, no sleep.
    clock["now"] += 30
    assert variables.read_cpu_pct() == 50 and len(sleeps) == 1
    # Within a second: the same value again (a 1-jiffy window would just be noise).
    clock["now"] += 0.2
    assert variables.read_cpu_pct() == 50
    # A stale baseline (over 2 min) is re-sampled rather than averaged over minutes.
    clock["now"] += 600
    assert variables.read_cpu_pct() == 0 and len(sleeps) == 2  # a fresh 100 jiffies, all idle


def test_cpu_is_none_where_proc_stat_is_missing(monkeypatch):
    from types import SimpleNamespace

    from stonepi_display import variables

    monkeypatch.setattr(variables, "_cpu_state", {"ticks": None, "at": 0.0, "value": None})
    monkeypatch.setattr(variables, "os", SimpleNamespace(name="posix"))

    def _missing(path="/proc/stat"):
        raise OSError("no /proc")

    monkeypatch.setattr(variables, "_proc_stat_ticks", _missing)
    assert variables.read_cpu_pct() is None
