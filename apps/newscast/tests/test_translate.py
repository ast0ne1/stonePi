from app.services.translate import (
    _chunks,
    _join_segments,
    _unpack_story,
    feed_translate_mode,
    llm_system_prompt,
    looks_untranslated,
    needs_translation,
    normalize_target_lang,
    parse_feed_translate_mode,
    resolve_provider,
    translate_story,
    translate_text,
    translate_to_english,
)


class FakeFeed:
    def __init__(self, translate=True, translate_provider="global"):
        self.translate = translate
        self.translate_provider = translate_provider


def test_parse_feed_translate_mode():
    assert parse_feed_translate_mode("off") == (False, "global")
    assert parse_feed_translate_mode("0") == (False, "global")
    assert parse_feed_translate_mode("1") == (True, "global")
    assert parse_feed_translate_mode("global") == (True, "global")
    assert parse_feed_translate_mode("google") == (True, "google")
    assert parse_feed_translate_mode("llm") == (True, "global")  # sources can no longer force AI


def test_resolve_provider_respects_feed_and_global():
    from app.services.ai import AI_OFF

    assert resolve_provider(None, FakeFeed(translate=False)) is None
    assert resolve_provider(None, FakeFeed(translate=True, translate_provider="global"), ai=AI_OFF) == "google"


def test_join_segments_reads_gtx_payload():
    payload = [[["The house is red.", "Huset er rødt", None, None, 10]], None, "da"]
    assert _join_segments(payload) == "The house is red."


def test_join_segments_reads_chrome_payload():
    assert _join_segments([["The airport closed.", "da"]]) == "The airport closed."
    assert _join_segments(["The airport closed.", "da"]) == "The airport closed."


def test_join_segments_rejects_junk():
    assert _join_segments(None) is None
    assert _join_segments([]) is None
    assert _join_segments("nope") is None


def test_chunks_keeps_short_text():
    assert _chunks("short") == ["short"]


def test_chunks_splits_long_text():
    text = ("word " * 1200).strip()
    parts = _chunks(text)
    assert len(parts) > 1
    assert all(len(part) <= 4200 for part in parts)


def test_looks_untranslated_detects_danish():
    assert looks_untranslated("Broarbejde lukker Storebæltsbroen")
    assert looks_untranslated("Mistanke om drone lukkede lufthavn")
    assert not looks_untranslated("Bridge work briefly closes the Great Belt Bridge")
    assert not looks_untranslated("Sund & Bælt writes that Johan Søe won bronze")


def test_translate_to_english_uses_google_post(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [[["The prime minister resigned.", "Statsministeren gik af", None, None, 3]]]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, params, data):
            assert "translate.googleapis.com" in url
            assert params["tl"] == "en"
            assert data["q"] == "Statsministeren gik af"
            return FakeResponse()

        def get(self, *_args, **_kwargs):
            raise AssertionError("chrome fallback should not run")

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    assert translate_to_english("Statsministeren gik af") == "The prime minister resigned."


def test_translate_falls_back_to_chrome(monkeypatch):
    class Limited:
        status_code = 429

    class Chrome:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [["The airport closed.", "da"]]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return Limited()

        def get(self, url, params):
            assert "clients5.google.com" in url
            assert params["q"] == "Lufthavnen lukkede"
            return Chrome()

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.translate.time.sleep", lambda *_args: None)
    assert translate_to_english("Lufthavnen lukkede") == "The airport closed."


def test_translate_story_keeps_original_when_request_fails(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            raise RuntimeError("blocked")

        def get(self, *_args, **_kwargs):
            raise RuntimeError("blocked")

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.translate.time.sleep", lambda *_args: None)
    title, excerpt = translate_story("Huset brænder", "Politiet er på vej.", provider="google")
    assert title == "Huset brænder"
    assert excerpt == "Politiet er på vej."


def test_normalize_target_lang_defaults_to_english():
    assert normalize_target_lang(None) == "en"
    assert normalize_target_lang("ES") == "es"
    assert normalize_target_lang("zz") == "en"


def test_needs_translation_uses_content_lang():
    class Story:
        def __init__(self, content_lang=None):
            self.content_lang = content_lang

    assert needs_translation(Story(None), "en")
    assert not needs_translation(Story("en"), "en")
    assert needs_translation(Story("en"), "es")


def test_translate_text_uses_spanish_tl(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [[["El primer ministro dimitió.", "Statsministeren gik af", None, None, 3]]]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, params, data):
            assert params["tl"] == "es"
            return FakeResponse()

        def get(self, *_args, **_kwargs):
            raise AssertionError("chrome fallback should not run")

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    assert translate_text("Statsministeren gik af", target_lang="es") == "El primer ministro dimitió."


def test_llm_system_prompt_mentions_spanish():
    prompt = llm_system_prompt("es")
    assert "Spanish" in prompt
    assert "English" not in prompt


def test_translate_with_llm(monkeypatch):
    from tests.ai_helpers import platform_ai

    sent = platform_ai(monkeypatch, "[[T]]\nThe house is burning\n[[B]]\nPolice are on the way.")
    record: dict = {}
    title, excerpt = translate_story(
        "Huset brænder",
        "Politiet er på vej.",
        provider="llm",
        target_lang="es",
        record=record,
    )
    assert title == "The house is burning"
    assert "Police" in excerpt
    assert "Spanish" in sent[0]["messages"][0]["content"]
    assert record["by"] == "ai" and record["model_id"] == "openai/gpt-4.1-mini"


def test_failed_ai_translation_falls_back_to_google(monkeypatch):
    import httpx

    from tests.ai_helpers import platform_ai

    platform_ai(monkeypatch, lambda prompt, system: httpx.Response(429, json={"error": {"message": "slow down"}}))
    monkeypatch.setattr(
        "app.services.translate._translate_chunk_google",
        lambda text, target_lang="en": text.replace("Huset brænder", "The house is burning"),
    )
    record: dict = {}
    title, _excerpt = translate_story("Huset brænder", "", provider="llm", record=record)
    assert title == "The house is burning"
    assert record == {"by": "fallback", "reason": "unavailable"}


def test_sources_can_only_opt_out_of_ai():
    from app.services.ai import AiSettings
    from app.services.translate import translate_record

    ai = AiSettings(available=True, enabled=True, summaries=False, translate=True, importance=False)
    off = AiSettings(available=True, enabled=True, summaries=False, translate=False, importance=False)
    google = FakeFeed(translate=True, translate_provider="google")
    legacy_ai = FakeFeed(translate=True, translate_provider="llm")  # older "Translate with AI"
    assert resolve_provider(None, google, ai=ai) == "google"
    assert translate_record(google, "google") == {"by": "opted_out"}
    assert resolve_provider(None, legacy_ai, ai=off) == "google"
    assert resolve_provider(None, legacy_ai, ai=ai) == "llm"
    assert translate_record(FakeFeed(), "google") == {"by": "fallback", "reason": "off"}


def test_unpack_story_splits_markers():
    assert _unpack_story("[[T]]\nThe house\n[[B]]\nPolice are coming.", "Huset", "Politiet") == (
        "The house",
        "Police are coming.",
    )


def test_translate_empty_returns_empty():
    assert translate_to_english("  ") == ""
