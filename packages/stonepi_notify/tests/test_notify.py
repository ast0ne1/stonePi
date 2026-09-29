from pathlib import Path

import stonepi_notify


def test_destinations_roundtrip(tmp_path: Path):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_notify.save_destinations(
        {
            "ntfy": {"enabled": True, "topic": "stonepi", "server": "https://ntfy.sh"},
            "trmnl": {"enabled": False, "display_id": "dashboard"},
        }
    )
    cfg = stonepi_notify.load_destinations()
    assert cfg["ntfy"]["enabled"] is True
    assert cfg["ntfy"]["topic"] == "stonepi"


def test_ingest_invalid():
    stonepi_notify.configure(data_dir=Path("/tmp/stonepi-notify-test-unused"))
    # reconfigure with tmp
    pass


def test_ingest_and_history(tmp_path: Path):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_notify.save_destinations({"ntfy": {"enabled": False, "topic": "t"}})
    result = stonepi_notify.ingest_event(
        {
            "id": "pricewatch.target_reached",
            "source": "pricewatch",
            "title": "Deal",
            "summary": "Cheap",
            "severity": "success",
            "dedupe_key": "pw:1",
        }
    )
    assert result.get("ok") is True
    hist = stonepi_notify.load_history()
    assert hist
    assert hist[0]["channel"] == "ntfy"
