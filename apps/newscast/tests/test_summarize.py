from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services.ai import AI_OFF, AiSettings
from app.services.summarize import clean_summary, fallback_summary, summarize_story
from tests.ai_helpers import platform_ai

AI_SUMMARIES = AiSettings(available=True, enabled=True, summaries=True, translate=False, importance=False)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ai_off_uses_on_device_summary_and_records_it():
    text, record = summarize_story("Headline", "A longer excerpt about the story.", "BBC", AI_OFF)
    assert text == "A longer excerpt about the story."
    assert record == {"by": "fallback", "reason": "off"}


def test_ai_summary_records_the_model(monkeypatch):
    sent = platform_ai(monkeypatch, "The athlete finished the race.")
    text, record = summarize_story("Race", "Source body", "BBC", AI_SUMMARIES)
    assert text == "The athlete finished the race."
    assert record == {"by": "ai", "model": "GPT-4.1 mini", "model_id": "openai/gpt-4.1-mini"}
    assert sent[0]["max_tokens"] == 220 and "Headline: Race" in sent[0]["messages"][1]["content"]


def test_every_model_failing_falls_back_on_device(monkeypatch):
    import httpx

    platform_ai(monkeypatch, lambda prompt, system: httpx.Response(503, json={"error": {"message": "down"}}))
    text, record = summarize_story("Headline", "A longer excerpt about the story.", "BBC", AI_SUMMARIES)
    assert text == "A longer excerpt about the story."
    assert record == {"by": "fallback", "reason": "unavailable"}


def test_clean_summary_strips_ai_preamble():
    raw = 'Here is a concise news briefing based on the provided text: "Markets rose after the central bank held rates."'
    assert clean_summary(raw) == "Markets rose after the central bank held rates."
    assert clean_summary("Here's a summary:\nSweden's left-wing bloc leads.") == "Sweden's left-wing bloc leads."
    assert clean_summary("Based on the provided text — China criticised AI competition claims.") == (
        "China criticised AI competition claims."
    )
    plain = "The boy was acquitted on grounds of insanity."
    assert clean_summary(plain) == plain


def test_summarize_story_strips_model_preamble(monkeypatch):
    platform_ai(monkeypatch, "Here is a concise news briefing based on the provided text: The athlete finished the race.")
    text, _record = summarize_story("Race", "Source body", "BBC", AI_SUMMARIES)
    assert text == "The athlete finished the race."
    assert "here is" not in text.lower()


def test_fallback_summary_clips():
    long = "word " * 200
    clipped = fallback_summary("Title", long)
    assert clipped.endswith("…")
    assert len(clipped) <= 420


NEWS_STORY = """Shares in the carmaker fell sharply on Monday after it warned that profits would be lower than expected this year.
The firm blamed weak demand in China and rising costs for raw materials, which it said had squeezed margins.
Analysts said the warning was the third this year and raised questions about the strategy of its chief executive.
Photo: Reuters
The chief executive, who took over in 2023, said the carmaker would cut 2,000 jobs and close two factories in Germany.
Investors have grown impatient with the slow pace of change at the carmaker, according to one fund manager.
The carmaker will publish full results in March, when it is expected to set out new profit targets."""

DANISH_STORY = """Regeringen vil hæve skatten på flyrejser fra næste år, oplyser skatteministeren på et pressemøde mandag.
Afgiften skal ifølge regeringen give omkring to milliarder kroner om året til den grønne omstilling.
Oppositionen kritiserer forslaget og siger, at det rammer familier med lave indkomster hårdest.
Branchen for luftfart advarer om, at flere danskere vil flyve fra lufthavne i Sverige og Tyskland i stedet.
Forslaget skal forhandles i Folketinget efter sommerferien, og der er endnu ikke et flertal for det."""


def _sentences(summary: str) -> list[str]:
    import re

    return [part for part in re.split(r"(?<=[.!?])\s+", summary) if part]


def test_on_device_summary_picks_verbatim_key_sentences():
    from app.services.summarize import on_device_summary

    summary = on_device_summary("Carmaker warns on profits", NEWS_STORY)
    parts = _sentences(summary)
    assert 2 <= len(parts) <= 3
    assert all(part in NEWS_STORY for part in parts)
    assert parts[0].startswith("Shares in the carmaker fell")  # the lede is always kept
    assert "Photo: Reuters" not in summary
    assert summary != fallback_summary("Carmaker warns on profits", NEWS_STORY)


def test_on_device_summary_keeps_article_order():
    from app.services.summarize import on_device_summary

    parts = _sentences(on_device_summary("Carmaker warns on profits", NEWS_STORY))
    positions = [NEWS_STORY.index(part) for part in parts]
    assert positions == sorted(positions)


def test_on_device_summary_drops_to_two_when_three_run_long():
    from app.services.summarize import SUMMARY_MAX_CHARS, on_device_summary

    filler = (
        "and it went on to describe the long and detailed history of the dispute in the region, "
        "naming the officials, the dates and the sums of money involved over many years"
    )
    long_story = "\n".join(
        f"Sentence {n} says the carmaker {word} the claims {filler}." for n, word in enumerate(
            ["denied", "rejected", "disputed", "questioned", "contested", "challenged"]
        )
    )
    summary = on_device_summary("Carmaker", long_story)
    assert len(_sentences(summary)) == 2
    assert len(summary) <= SUMMARY_MAX_CHARS


def test_on_device_summary_short_text_keeps_excerpt():
    from app.services.summarize import on_device_summary

    short = "Body text that is short. It has two sentences."
    assert on_device_summary("Headline", short) == short
    assert on_device_summary("Headline", "") == "Headline"


def test_on_device_summary_falls_back_when_sumy_fails(monkeypatch):
    from app.services import summarize

    def boom(*_args, **_kwargs):
        raise LookupError("no punkt")

    monkeypatch.setattr(summarize, "lexrank_sentences", boom)
    assert summarize.on_device_summary("Headline", NEWS_STORY) == fallback_summary("Headline", NEWS_STORY)


def test_on_device_summary_falls_back_without_whole_sentences():
    from app.services.summarize import on_device_summary

    no_sentences = " ".join(["breaking news headline without a full stop"] * 15)
    assert on_device_summary("Headline", no_sentences) == fallback_summary("Headline", no_sentences)


def test_on_device_summary_strips_repeated_headline():
    from app.services.summarize import on_device_summary

    title = "Carmaker warns on profits"
    summary = on_device_summary(title, f"{title} - Published {NEWS_STORY}")
    assert summary.startswith("Shares in the carmaker fell")


def test_unusable_sentences_are_skipped():
    from app.services.summarize import _usable_sentence

    assert _usable_sentence("The firm blamed weak demand in China for the fall in profits.")
    assert not _usable_sentence("Photo: Reuters")
    assert not _usable_sentence("alongside a logo that did not lead to any hits in the databases.")
    assert not _usable_sentence('[It] swaps colors really quickly as well," Knowles says.')
    assert not _usable_sentence("A heading that runs on without any closing punctuation at all")


def test_danish_story_uses_danish_tools():
    from app.services.summarize import _sumy_tools, guess_language, on_device_summary

    assert guess_language(DANISH_STORY) == "danish"
    assert guess_language(NEWS_STORY) == "english"
    _tokenizer, summarizer = _sumy_tools("danish")
    assert "og" in summarizer.stop_words and "ikke" in summarizer.stop_words
    assert not any(word.startswith("#") for word in summarizer.stop_words)
    parts = _sentences(on_device_summary("Flyskat", DANISH_STORY))
    assert 2 <= len(parts) <= 3
    assert all(part in DANISH_STORY for part in parts)


def test_punkt_data_is_bundled_and_never_downloaded(monkeypatch):
    import nltk

    from app.services.summarize import NLTK_DATA_DIR, _sumy_tools, lexrank_sentences

    for language in ("english", "danish"):
        assert (NLTK_DATA_DIR / "tokenizers" / "punkt_tab" / language / "sent_starters.txt").exists()
    assert (NLTK_DATA_DIR / "danish_stopwords.txt").exists()

    def no_download(*_args, **_kwargs):
        raise AssertionError("NLTK data must not be downloaded at runtime")

    monkeypatch.setattr(nltk, "download", no_download)
    _sumy_tools.cache_clear()
    assert lexrank_sentences(NEWS_STORY)
    assert str(NLTK_DATA_DIR) in nltk.data.path
    found = nltk.data.find("tokenizers/punkt_tab/english/")
    assert str(NLTK_DATA_DIR) in str(found)


def test_ai_off_uses_on_device_summary():
    summary, _record = summarize_story("Carmaker warns on profits", NEWS_STORY, "BBC", AI_OFF)
    assert summary.startswith("Shares in the carmaker fell")
    assert summary != fallback_summary("Carmaker warns on profits", NEWS_STORY)


def test_on_device_summary_reads_only_the_start_of_long_texts(monkeypatch):
    from app.services import summarize

    seen = {}

    def fake_lexrank(text, title=""):
        seen["text"] = text
        return ["First picked sentence of the story.", "Second picked sentence of the story."]

    monkeypatch.setattr(summarize, "lexrank_sentences", fake_lexrank)
    long_text = "\n".join([NEWS_STORY] * 40)
    assert len(long_text) > summarize.SUMMARY_INPUT_CHARS
    summarize.on_device_summary("Carmaker", long_text)
    assert len(seen["text"]) <= summarize.SUMMARY_INPUT_CHARS
    assert long_text.startswith(seen["text"])
    assert seen["text"].endswith(".")  # cut at a line break, not mid-sentence
