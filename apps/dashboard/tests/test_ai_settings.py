"""Settings → AI (backlog F13): providers, tiers, per-job lists, Gemini's Free question, quota."""

from __future__ import annotations

import json

import pytest

from app import routes
from stonepi_ai import config
from tests.test_recover_password_settings import FakeVault, _client


@pytest.fixture
def ai_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STONEPI_AI_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "RECOVER_PASSWD_FILE", tmp_path / "recover.passwd")
    return tmp_path


def _keys(monkeypatch, **keys):
    monkeypatch.setattr(config, "secret_for", lambda pid: keys.get(pid, ""))


def test_ai_tab_listed_under_house(monkeypatch, ai_dir):
    html = _client(monkeypatch, FakeVault(), []).get("/settings").text
    assert 'href="/settings?tab=ai"' in html


def test_page_without_providers_says_so(monkeypatch, ai_dir):
    _keys(monkeypatch)
    html = _client(monkeypatch, FakeVault(), []).get("/settings?tab=ai").text
    assert "No AI provider is set up yet" in html
    assert "data-ai-page" in html and 'data-ai-rank="tier:best"' in html
    assert 'data-ai-function="newscast.summary"' in html and 'data-ai-function="studio.game"' in html


def test_gemini_key_asks_free_or_paid(monkeypatch, ai_dir):
    _keys(monkeypatch, gemini="AIza")
    client = _client(monkeypatch, FakeVault(), [])
    assert "Free tier, no billing on this key?" in client.get("/settings?tab=ai").text
    resp = client.post("/settings/ai", data={"action": "gemini_free:yes"}, follow_redirects=False)
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    cfg = config.load()
    assert cfg["providers"]["gemini"]["free_tier"] is True
    assert config.is_free(config.entry_by_id(cfg)["gemini/gemini-3.5-flash-lite"], cfg)
    assert "Free tier, no billing on this key?" not in client.get("/settings?tab=ai").text


def test_save_state_reorders_tiers_and_overrides_a_job(monkeypatch, ai_dir):
    _keys(monkeypatch, openai="sk", anthropic="sk-ant")
    state = json.loads(json.dumps({k: v for k, v in config.default_config().items() if k in {"models", "tiers"}}))
    state["tiers"]["best"] = ["openai/gpt-4.1", "anthropic/claude-sonnet-5"]
    state["functions"] = {
        "studio.game": {"models": ["anthropic/claude-opus-5"]},
        "newscast.summary": {"tier": "cheap"},  # same as suggested: not stored
        "newscast.importance": {"tier": "standard"},
    }
    resp = _client(monkeypatch, FakeVault(), []).post(
        "/settings/ai", data={"action": "save", "state_json": json.dumps(state)}, follow_redirects=False
    )
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    cfg = config.load()
    assert cfg["tiers"]["best"] == ["openai/gpt-4.1", "anthropic/claude-sonnet-5"]
    assert cfg["functions"] == {"studio.game": {"models": ["anthropic/claude-opus-5"]}, "newscast.importance": {"tier": "standard"}}
    assert [m["id"] for m in config.route("studio.game", cfg)] == ["anthropic/claude-opus-5"]


def test_free_model_in_best_shows_warning(monkeypatch, ai_dir):
    _keys(monkeypatch, gemini="AIza")
    cfg = config.default_config()
    cfg["providers"]["gemini"]["free_tier"] = True
    cfg["tiers"]["best"] = ["gemini/gemini-3.5-flash-lite"]
    config.save(cfg)
    html = _client(monkeypatch, FakeVault(), []).get("/settings?tab=ai").text
    assert "is in the Best tier" in html


def test_bad_ollama_url_and_quota_limit(monkeypatch, ai_dir):
    _keys(monkeypatch)
    client = _client(monkeypatch, FakeVault(), [])
    resp = client.post("/settings/ai", data={"action": "ollama_url", "ollama_url": "pi.local:11434"}, follow_redirects=False)
    assert "err=" in resp.headers["location"]
    client.post("/settings/ai", data={"action": "ollama_url", "ollama_url": "http://pi.local:11434"})
    assert config.load()["providers"]["ollama"]["base_url"] == "http://pi.local:11434"
    assert config.provider_ready("ollama")
    client.post("/settings/ai", data={"action": "quota", "quota_limit": "500"})
    from stonepi_vault.quota import get_quota

    assert get_quota("llm", root=ai_dir).status()["limit"] == 500


def test_vault_ai_group_has_gemini_and_no_studio_url(monkeypatch, ai_dir):
    html = _client(monkeypatch, FakeVault(), []).get("/settings?tab=vault").text
    assert 'id="vault-GEMINI_API_KEY"' in html
    assert 'id="vault-STUDIO_LLM_BASE_URL"' not in html


def test_model_label_cannot_close_the_script_block(monkeypatch, ai_dir):
    _keys(monkeypatch, openai="sk")
    cfg = config.default_config()
    cfg["models"].append(config.model_entry("openai", "gpt-x", label="</script><img src=x onerror=alert(1)>"))
    config.save(cfg)
    html = _client(monkeypatch, FakeVault(), []).get("/settings?tab=ai").text
    assert "</script><img" not in html
