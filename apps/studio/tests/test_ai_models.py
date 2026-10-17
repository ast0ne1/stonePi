"""Studio builds through the platform AI config (stonepi_ai), one function per build kind."""

from __future__ import annotations

import json

import pytest

from app import llm


@pytest.fixture(autouse=True)
def ai_dir(tmp_path, monkeypatch):
    from stonepi_ai import config

    monkeypatch.setenv("STONEPI_AI_DIR", str(tmp_path / "ai"))
    keys: dict[str, str] = {}
    monkeypatch.setattr(config, "secret_for", lambda pid: keys.get(pid, ""))
    monkeypatch.setattr(llm, "LEGACY_SETTINGS", tmp_path / "settings.json")
    return keys


def test_each_kind_is_its_own_function():
    assert llm.function_for("game") == "studio.game"
    assert llm.function_for("guide") == "studio.guide"
    assert llm.function_for("nonsense") == "studio.spa"


def test_overview_lists_models_per_kind(ai_dir):
    assert all(row["models"] == [] for row in llm.model_overview())
    ai_dir["anthropic"] = "sk-ant"
    rows = {row["kind"]: row["models"] for row in llm.model_overview()}
    assert rows["game"] == ["Claude Sonnet 5"]
    assert rows["guide"] == ["Claude Haiku 4.5"]


def test_no_model_raises_a_clear_error():
    from stonepi_ai import AIUnavailable

    with pytest.raises(AIUnavailable):
        list(llm.stream_chat([{"role": "user", "content": "hi"}], [], kind="game"))


def test_legacy_choice_moves_to_platform_config(tmp_path):
    from stonepi_ai import config

    (tmp_path / "settings.json").write_text(
        json.dumps({"provider": "openai", "anthropic_model": "claude-sonnet-5", "openai_model": "gpt-4.1-mini"}),
        encoding="utf-8",
    )
    assert llm.migrate_legacy_models()
    cfg = config.load()
    for fid in ("studio.spa", "studio.guide", "studio.game"):
        assert cfg["functions"][fid] == {"models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-5"]}
    assert not (tmp_path / "settings.json").exists()
    assert not llm.migrate_legacy_models()  # once


def test_legacy_custom_model_id_is_added(tmp_path):
    from stonepi_ai import config

    (tmp_path / "settings.json").write_text(json.dumps({"anthropic_model": "claude-next-1"}), encoding="utf-8")
    llm.migrate_legacy_models()
    cfg = config.load()
    assert "anthropic/claude-next-1" in config.entry_by_id(cfg)
    assert cfg["functions"]["studio.game"]["models"][0] == "anthropic/claude-next-1"
