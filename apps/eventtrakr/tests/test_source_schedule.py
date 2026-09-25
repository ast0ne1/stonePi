from types import SimpleNamespace

from app.services import settings


def test_normalize_schedule_weekly_and_interval():
    weekly = settings.normalize_schedule_config(
        {"mode": "weekly", "days": ["mon", "bogus", "fri"], "times": ["09:00", "18:30", "bad"]}
    )
    assert weekly == {"mode": "weekly", "days": ["mon", "fri"], "times": ["09:00", "18:30"]}

    interval = settings.normalize_schedule_config({"mode": "interval", "interval_minutes": 10})
    assert interval == {"mode": "interval", "interval_minutes": 15}


def test_get_source_schedule_falls_back_to_interval_minutes():
    src = SimpleNamespace(schedule_mode="custom", schedule_config="", interval_minutes=120)
    assert settings.get_source_schedule(src) == {"mode": "interval", "interval_minutes": 120}

    global_src = SimpleNamespace(schedule_mode="global", schedule_config="", interval_minutes=120)
    assert settings.get_source_schedule(global_src) is None


def test_apply_source_schedule_persists_weekly_json():
    src = SimpleNamespace(schedule_mode="global", schedule_config="", interval_minutes=None)
    settings.apply_source_schedule(
        src, {"mode": "weekly", "days": ["tue", "thu"], "times": ["08:00", "20:00"]}
    )
    assert src.schedule_mode == "custom"
    assert src.interval_minutes is None
    assert '"mode": "weekly"' in src.schedule_config or '"mode":"weekly"' in src.schedule_config.replace(" ", "")
    loaded = settings.get_source_schedule(src)
    assert loaded["mode"] == "weekly"
    assert loaded["days"] == ["tue", "thu"]
    assert loaded["times"] == ["08:00", "20:00"]
