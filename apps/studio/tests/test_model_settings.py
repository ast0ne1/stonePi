"""Admin model/provider choice and how it drives the LLM call."""

from __future__ import annotations

import pytest

from app import llm, model_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(model_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(model_settings, "DATA_DIR", tmp_path)
    keys = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "STUDIO_LLM_BASE_URL": ""}
    monkeypatch.setattr(llm, "_secret", lambda name: keys.get(name, ""))
    return keys


def test_defaults_without_saved_settings():
    assert model_settings.provider_pref() == "auto"
    assert model_settings.anthropic_model() == "claude-sonnet-5"
    assert llm.active_provider()["id"] == ""


def test_openai_only_key_is_used_automatically(isolated):
    isolated["OPENAI_API_KEY"] = "sk-test"
    model_settings.save(openai="gpt-4.1-mini")
    active = llm.active_provider()
    assert active["id"] == "openai" and active["model"] == "gpt-4.1-mini"


def test_explicit_provider_wins_when_both_keys_exist(isolated):
    isolated["OPENAI_API_KEY"] = "sk-test"
    isolated["ANTHROPIC_API_KEY"] = "sk-ant-test"
    assert llm.active_provider()["id"] == "anthropic"  # auto prefers Anthropic
    model_settings.save(provider="openai")
    assert llm.active_provider()["id"] == "openai"


def test_chosen_provider_without_key_falls_back(isolated):
    isolated["OPENAI_API_KEY"] = "sk-test"
    model_settings.save(provider="anthropic", anthropic="claude-opus-5")
    assert llm.active_provider()["id"] == "openai"


def test_rejects_bad_model_ids():
    with pytest.raises(ValueError):
        model_settings.save(anthropic="")
    with pytest.raises(ValueError):
        model_settings.save(openai="gpt 4 <script>")
    with pytest.raises(ValueError):
        model_settings.save(provider="gemini")


def test_reset_restores_defaults():
    model_settings.save(provider="openai", anthropic="claude-haiku-4-5", openai="gpt-4o")
    model_settings.reset()
    assert model_settings.provider_pref() == "auto"
    assert model_settings.anthropic_model() == "claude-sonnet-5"


def test_settings_model_tab_and_save(monkeypatch, isolated):
    from fastapi.testclient import TestClient

    from app import routes
    from app.main import app

    isolated["OPENAI_API_KEY"] = "sk-secret-value"
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "csrf_ok", lambda *_a: True)
    client = TestClient(app)

    page = client.get("/settings?tab=model").text
    assert "Key found" in page and "sk-secret-value" not in page

    resp = client.post(
        "/settings/model",
        data={"provider": "openai", "anthropic_model": "custom", "anthropic_custom": "claude-opus-5", "openai_model": "gpt-4o"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and "msg=" in resp.headers["location"]
    assert model_settings.provider_pref() == "openai"
    assert model_settings.anthropic_model() == "claude-opus-5"
    assert model_settings.openai_model() == "gpt-4o"
    assert "<code>gpt-4o</code>" in client.get("/settings?tab=model").text


def test_model_panels_follow_provider_and_openai_custom(monkeypatch, isolated):
    import re

    from fastapi.testclient import TestClient

    from app import routes
    from app.main import app

    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "csrf_ok", lambda *_a: True)
    client = TestClient(app)

    def open_panels():
        page = client.get("/settings?tab=model").text
        return sorted(re.findall(r'data-model-panel="(\w+)" open', page))

    assert open_panels() == ["anthropic", "openai"]  # automatic shows both

    client.post("/settings/model", data={
        "provider": "openai", "anthropic_model": "claude-sonnet-5",
        "openai_model": "custom", "openai_custom": "gpt-5-nano",
    })
    assert model_settings.openai_model() == "gpt-5-nano"
    assert open_panels() == ["openai"]

    client.post("/settings/model", data={
        "provider": "anthropic", "anthropic_model": "claude-haiku-4-5", "openai_model": "gpt-5-mini",
    })
    assert model_settings.openai_model() == "gpt-5-mini"
    assert open_panels() == ["anthropic"]
    assert 'value="gpt-5-mini" checked' in client.get("/settings?tab=model").text
