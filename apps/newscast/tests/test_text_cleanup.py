"""Strict text cleanup: precision extraction, page-furniture removal, one-off stored tidy-up."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story
from app.services import text_cleanup
from app.services.text_cleanup import extract_text, run_tidy_once, tidy_lines

# Real summary from The Age (a video page), as stored before this change.
AGE_VIDEO = (
    "Advertisement\n"
    "Beau Lamarre-Condon double murder trial begins\n"
    "The double murder trial of police officer Beau Lamarre-Condon has begun with the accused man "
    "offering to plead guilty to the manslaughter of one of the victims.\n"
    "Updated ,first published\n"
    "Loading\n"
    "Latest in Videos\n"
    "Advertisement"
)


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_tidy_lines_drops_furniture_and_repeated_headline():
    cleaned = tidy_lines(AGE_VIDEO, title="Beau Lamarre-Condon double murder trial begins")
    assert cleaned == (
        "The double murder trial of police officer Beau Lamarre-Condon has begun with the accused man "
        "offering to plead guilty to the manslaughter of one of the victims."
    )


def test_tidy_lines_keeps_real_sentences_with_the_same_words():
    text = "Loading bay fire injures two\nAdvertisement spending fell in August.\nUpdated figures are due Friday."
    assert tidy_lines(text) == text


def test_strict_falls_back_to_standard_when_it_finds_nothing(monkeypatch):
    calls: list[bool] = []

    def fake_extract(html, **kwargs):
        calls.append(bool(kwargs.get("favor_precision")))
        return "" if kwargs.get("favor_precision") else "Standard text\nAdvertisement"

    monkeypatch.setattr(text_cleanup.trafilatura, "extract", fake_extract)
    assert extract_text("<html></html>", strict=False) == ("Standard text\nAdvertisement", False)
    text, fell_back = extract_text("<html></html>", strict=True)
    assert (text, fell_back) == ("Standard text", True)  # still tidied
    assert calls == [False, True, False]


def test_strict_uses_precision_text_when_available(monkeypatch):
    monkeypatch.setattr(
        text_cleanup.trafilatura, "extract",
        lambda html, **kw: "Precise body\nLoading" if kw.get("favor_precision") else "noisy",
    )
    assert extract_text("<html></html>", strict=True) == ("Precise body", False)


def test_one_off_tidy_cleans_stored_stories_once():
    db = _session()
    title = "Beau Lamarre-Condon double murder trial begins"
    db.add_all(
        [
            Story(
                title=title, summary=AGE_VIDEO, raw_excerpt=AGE_VIDEO, source_name="The Age",
                canonical_url="https://example.com/age", content_hash="a", cluster_key="a",
            ),
            Story(
                title="Only furniture", summary="Advertisement\nLoading", raw_excerpt="", source_name="The Age",
                canonical_url="https://example.com/empty", content_hash="b", cluster_key="b",
            ),
            Story(
                title="Clean", summary="Nothing to remove here.", raw_excerpt="", source_name="Hackaday",
                canonical_url="https://example.com/clean", content_hash="c", cluster_key="c",
            ),
        ]
    )
    db.commit()

    assert run_tidy_once(db) == 2
    age = db.query(Story).filter(Story.content_hash == "a").one()
    assert "Advertisement" not in age.summary and "Loading" not in age.summary
    assert age.summary.startswith("The double murder trial")
    assert db.query(Story).filter(Story.content_hash == "b").one().summary == "Only furniture"  # title kept
    assert db.query(Story).filter(Story.content_hash == "c").one().summary == "Nothing to remove here."
    assert run_tidy_once(db) == 0  # recorded as done
