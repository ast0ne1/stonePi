import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You write concise news briefings for an e-ink reader. "
    "Reply with only the summary text: 2-4 factual sentences. "
    "Do not add a preamble, greeting, title, or closing. "
    "Never start with phrases like 'Here is', 'Here's', 'Sure', "
    "'Based on the provided text', or 'A concise summary'. "
    "No hype, no clickbait. Mention the outlet only if it adds context. "
    "Do not invent facts that are not in the source text."
)

_PREAMBLE_RE = re.compile(
    r"""
    ^\s*[\"'“”`]?
    (?:(?:sure|okay|ok|certainly|of\ course)[,!]?\s+)?
    (?:
        here(?:'s|\s+is)\s+(?:a\s+)?(?:concise\s+)?(?:news\s+)?(?:briefing|summary|overview|recap)
            (?:\s+based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)?)?
        |here(?:'s|\s+is)\s+(?:the\s+)?(?:briefing|summary|overview|recap)
        |based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)
        |(?:a\s+)?concise\s+(?:news\s+)?(?:briefing|summary)
            (?:\s+based\s+on(?:\s+the)?(?:\s+provided|\s+source|\s+given)?(?:\s+text|\s+article|\s+story|\s+content)?)?
        |i(?:'ve|\s+have)\s+(?:written|prepared|created)\s+(?:a\s+)?(?:briefing|summary)
    )
    [^\n:]{0,80}
    [:\-–—]\s*
    [\"'“”`]?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WRAPPER_RE = re.compile(r'^[\"“”\'`]+|[\"“”\'`]+$')


def clean_summary(text: str, *, title: str = "", excerpt: str = "") -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return fallback_summary(title, excerpt)

    if len(cleaned) >= 2 and cleaned[0] in "\"“'`" and cleaned[-1] in "\"”'`":
        cleaned = cleaned[1:-1].strip()

    for _ in range(3):
        next_text = _PREAMBLE_RE.sub("", cleaned, count=1).strip()
        next_text = _WRAPPER_RE.sub("", next_text).strip()
        if next_text == cleaned:
            break
        cleaned = next_text

    lowered = cleaned.lower()
    if not cleaned or lowered.startswith(("here is", "here's", "based on the provided")):
        return fallback_summary(title, excerpt)
    return cleaned


def fallback_summary(title: str, excerpt: str) -> str:
    text = (excerpt or "").strip()
    if not text:
        return title.strip()
    compact = " ".join(text.split())
    if len(compact) <= 420:
        return compact
    clipped = compact[:417].rsplit(" ", 1)[0]
    return clipped + "…"


# NLTK punkt data ships inside the app (English + Danish), so nothing is downloaded at runtime.
NLTK_DATA_DIR = Path(__file__).resolve().parent.parent / "nltk_data"
SUMMARY_SENTENCES = 3
SUMMARY_MAX_CHARS = 480  # three sentences longer than this drop to two
SUMMARY_INPUT_CHARS = 8000  # LexRank grows with the square of the sentences; the key facts come early
_MIN_SENTENCE_CHARS = 40
_MAX_SENTENCE_CHARS = 400
_SENTENCE_END = ('.', '!', '?', '"', '”', '’', "'", ')')

_DANISH_HINTS = frozenset(
    "og ikke det er på med til af som har der fra blev også efter skal kan men han hun jeg vi de".split()
)
_ENGLISH_HINTS = frozenset("the and of to is was that with for on he she it by are has have".split())
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def guess_language(text: str) -> str:
    """``danish`` when the text reads as Danish (an untranslated source), else ``english``."""
    words = [word.lower() for word in _WORD_RE.findall(text[:3000])]
    danish = sum(1 for word in words if word in _DANISH_HINTS)
    english = sum(1 for word in words if word in _ENGLISH_HINTS)
    return "danish" if danish > english else "english"


@lru_cache(maxsize=4)
def _sumy_tools(language: str):
    import nltk

    data_dir = str(NLTK_DATA_DIR)
    if data_dir not in nltk.data.path:
        nltk.data.path.insert(0, data_dir)

    from sumy.nlp.stemmers import Stemmer
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.lex_rank import LexRankSummarizer
    from sumy.utils import get_stop_words, read_stop_words

    summarizer = LexRankSummarizer(Stemmer(language))
    if language == "danish":
        summarizer.stop_words = read_stop_words(str(NLTK_DATA_DIR / "danish_stopwords.txt"))
    else:
        summarizer.stop_words = get_stop_words(language)
    return Tokenizer(language), summarizer


def _usable_sentence(text: str) -> bool:
    """A whole sentence: sensible length, starts like one, ends like one, quotes balanced."""
    if not _MIN_SENTENCE_CHARS <= len(text) <= _MAX_SENTENCE_CHARS:
        return False
    if not text.endswith(_SENTENCE_END) or text[0].islower():
        return False
    return text.count('"') % 2 == 0 and text.count("“") == text.count("”")


def _pick_best(infos):
    """The lede plus the top-rated usable sentences (``infos`` arrive best first).

    News puts its key fact first and LexRank alone often skips it, so the first usable
    sentence always stays; LexRank picks the rest. Two sentences when three run long.
    """
    usable = []
    seen: set[str] = set()
    for info in infos:
        text = str(info.sentence).strip()
        if _usable_sentence(text) and text not in seen:
            seen.add(text)
            usable.append(info)
    if not usable:
        return []
    lede = min(usable, key=lambda info: info.order)
    chosen = [lede] + [info for info in usable if info is not lede][: SUMMARY_SENTENCES - 1]
    if len(chosen) == SUMMARY_SENTENCES and sum(len(str(i.sentence)) + 1 for i in chosen) > SUMMARY_MAX_CHARS:
        chosen.pop()
    return chosen


def _strip_title(text: str, title: str) -> str:
    """Drop a leading copy of the headline (BBC and NPR pages repeat it, BBC adds "- Published")."""
    head = " ".join((title or "").split())
    if not head:
        return text
    compact = text.lstrip()
    if not " ".join(compact[: len(head) + 40].split()).startswith(head):
        return text
    rest = compact[len(head):] if compact.startswith(head) else compact.split("\n", 1)[-1]
    return re.sub(r"^\s*(?:[-–—|:]\s*)?(?:Published\b\s*)?", "", rest)


def lexrank_sentences(text: str, title: str = "") -> list[str]:
    """The story's key sentences, verbatim and in article order (sumy LexRank, lede kept)."""
    from sumy.parsers.plaintext import PlaintextParser

    text = _strip_title(text, title)
    tokenizer, summarizer = _sumy_tools(guess_language(text))
    # One paragraph per line, so a heading or caption without a full stop stays on its own.
    paragraphs = "\n\n".join(line.strip() for line in text.splitlines() if line.strip())
    document = PlaintextParser.from_string(paragraphs, tokenizer).document
    return [str(sentence).strip() for sentence in summarizer(document, _pick_best)]


def on_device_summary(title: str, excerpt: str) -> str:
    """The non-AI summary: 2-3 key sentences picked on the Pi, or the excerpt when that fails."""
    compact = " ".join((excerpt or "").split())
    if len(compact) <= 420:
        return fallback_summary(title, excerpt)
    text = excerpt
    if len(text) > SUMMARY_INPUT_CHARS:
        text = text[:SUMMARY_INPUT_CHARS].rsplit("\n", 1)[0]
    try:
        picked = lexrank_sentences(text, title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("on-device summary failed for %s: %s", title, exc)
        picked = []
    if len(picked) < 2:
        return fallback_summary(title, excerpt)
    return " ".join(picked)


def summary_prompt(title: str, excerpt: str, source: str) -> str:
    return (
        f"Outlet: {source}\n"
        f"Headline: {title}\n\n"
        f"Source text:\n{(excerpt or title)[:4000]}\n\n"
        "Write only the summary sentences. No introduction."
    )


def summarize_story(title: str, excerpt: str, source: str, ai=None, db=None) -> tuple[str, dict]:
    """(summary, the story's summary record): AI when NewsCast uses it for summaries,
    otherwise, or when every model fails, the on-device summary."""
    from app.services.ai import FN_SUMMARY, by_ai, complete, fell_back

    if ai is None or not ai.summaries:
        return on_device_summary(title, excerpt), fell_back("off")
    result = complete(
        FN_SUMMARY,
        db,
        "Summary",
        system=SYSTEM_PROMPT,
        prompt=summary_prompt(title, excerpt, source),
        max_tokens=220,
        temperature=0.2,
    )
    if result is None:
        return on_device_summary(title, excerpt), fell_back("unavailable")
    cleaned = clean_summary(result.text, title=title, excerpt=excerpt)
    return cleaned or on_device_summary(title, excerpt), by_ai(result)
