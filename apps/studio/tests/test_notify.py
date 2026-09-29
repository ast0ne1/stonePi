"""Tests for Studio publish notifications."""

from __future__ import annotations

from app.services import notify as notify_service


def test_emit_site_published(monkeypatch):
    emitted: list[object] = []

    def _fake_emit(envelope):
        emitted.append(envelope)
        return True

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)
    assert notify_service.emit_site_published(
        title="Star Snake",
        kind="game",
        project_id="abc",
        public_url="http://pi/files/u/kid/star-snake/",
        is_update=False,
    )
    assert len(emitted) == 1
    assert emitted[0].id == "studio.site_published"
    assert "game" in emitted[0].summary.lower()
    assert "Star Snake" in emitted[0].title
