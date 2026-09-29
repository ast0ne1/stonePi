"""Tests for disabled-app watch overlay and Auth cookie forwarding helpers."""

from __future__ import annotations

from app import services


def test_apply_disabled_to_watch_clears_attention_for_hidden_apps():
    watch = {
        "level": "attention",
        "summary": "NewsCast is not running",
        "reasons": ["NewsCast is not running"],
        "apps": [
            {
                "id": "newscast",
                "n": "NewsCast",
                "enabled": True,
                "running": False,
                "level": "attention",
                "health_ok": False,
            },
            {
                "id": "auth",
                "n": "Auth",
                "enabled": True,
                "running": True,
                "level": "healthy",
                "health_ok": True,
            },
        ],
        "disk_pct": 10,
        "backup_status": "ok",
        "backup_age_days": 1.0,
    }
    out = services.apply_disabled_to_watch(watch, {"newscast"})
    assert out["level"] == "healthy"
    assert out["summary"] == "All clear"
    nc = next(a for a in out["apps"] if a["id"] == "newscast")
    assert nc["enabled"] is False
    assert nc["level"] == "healthy"


def test_launcher_tiles_prefers_saved_order(monkeypatch):
    class User:
        is_admin = True

        def can_access(self, _app_id):
            return True

    monkeypatch.setattr(services, "LAUNCHER_APP_IDS", ("newscast", "pinboard", "fileserve"))
    monkeypatch.setattr(
        services,
        "catalog_apps",
        lambda include_auth=False, cookies=None: [
            {"id": "newscast", "name": "NewsCast", "enabled": True, "path": "/news", "port": 8001},
            {"id": "pinboard", "name": "Pinboard", "enabled": True, "path": "/pinboard", "port": 8004},
            {"id": "fileserve", "name": "FileServe", "enabled": True, "path": "/files", "port": 8002},
        ],
    )
    monkeypatch.setattr(
        services,
        "auth_request",
        lambda *a, **k: {"launcher_order": ["pinboard", "newscast"]},
    )
    monkeypatch.setattr(services, "app_public_url", lambda item: item["path"])
    tiles = services.launcher_tiles(User(), cookies={"stonepi": "x"})
    assert [t["id"] for t in tiles] == ["pinboard", "newscast", "fileserve"]
