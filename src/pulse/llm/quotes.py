"""Verbatim quote selection, verified in code (architecture.md §3.6, Task 4.1).

The model only proposes a review and a span; code checks that the span is really in that review, and
falls back to a review's first sentence when the model can't produce a valid pick.
"""

import random
import re
from collections.abc import Sequence
from statistics import median
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from pulse.analysis.aggregate import ThemeStats
from pulse.config import PulseSettings
from pulse.llm.prompts import load_prompt, reviews_json
from pulse.llm.schema import LLMRefusal, LLMRequest, LLMSchemaError, LLMTruncated
from pulse.models import Classification, Quote, Review
from pulse.privacy.scrub import PLACEHOLDER_RE
from pulse.timeutil import Window, iso_week_of

QUOTABLE_LANGUAGES = frozenset({"en", "hinglish"})
SURROUNDING_QUOTES = "\"'“”‘’ \t\n"
SENTENCE_END = re.compile(r"(?<=[.!?])\s|\n")
FALLBACK_MIN_WORDS = 3
REASK_SUFFIX = "\n\nYour previous pick was rejected: {error}. Pick again."


class QuotePick(BaseModel):
    k: int  # the review's short key in the prompt
    quote: str


class QuoteUnavailable(Exception):
    def __init__(self, theme_id: str) -> None:
        super().__init__(f"no quotable review for theme {theme_id!r}")
        self.theme_id = theme_id


def normalize_quote_text(text: str) -> str:
    """Whitespace collapsed and curly quotes straightened, on both sides of the comparison (QUO-2, QUO-8)."""
    straightened = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(straightened.split())


def verify_quote(quote: str, text: str, max_words: int = 30) -> bool:
    """True only for a 1..max_words-word span that appears in `text` (QUO-1)."""
    stripped = quote.strip(SURROUNDING_QUOTES)
    words = len(stripped.split())
    return 1 <= words <= max_words and normalize_quote_text(stripped) in normalize_quote_text(text)


def first_sentence(text: str) -> str:
    return SENTENCE_END.split(text.strip(), maxsplit=1)[0].strip()


def candidate_pool(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    theme_id: str,
    reviews: Sequence[Review],
    cls_by_id: dict[str, Classification],
    window: Window,
    limits: PulseSettings,
    seed: int = 0,
) -> list[Review]:
    """Quotable reviews of one theme: latest week first, then closest to the theme's median rating."""
    members = [
        review
        for review in reviews
        if review.review_id in cls_by_id and cls_by_id[review.review_id].theme_id == theme_id
    ]
    if not members:
        return []
    middle = median(review.rating for review in members)
    pool = [review for review in members if _quotable(review, limits)]
    pool.sort(key=lambda review: review.review_id)
    random.Random(seed).shuffle(pool)
    pool.sort(key=lambda review: (not _in_latest_week(review, window), abs(review.rating - middle)))
    return pool[: limits.quote_candidates]


def select_quotes(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    llm: object,
    top: Sequence[ThemeStats],
    reviews: Sequence[Review],
    cls_by_id: dict[str, Classification],
    window: Window,
    limits: PulseSettings,
    *,
    seed: int = 0,
) -> tuple[list[Quote], int]:
    """One verified quote per top theme, and how many came from the first-sentence fallback (M-RUN-3)."""
    system = load_prompt("quotes").text
    quotes, fallbacks = [], 0
    for stats in top:
        pool = candidate_pool(stats.theme_id, reviews, cls_by_id, window, limits, seed)
        if not pool:
            raise QuoteUnavailable(stats.theme_id)
        picked = _ask(llm, system, stats, pool, limits)
        if picked is None:
            picked = _fallback(stats.theme_id, pool, limits)
            fallbacks += 1
        review, text = picked
        quotes.append(_quote(review, stats.theme_id, text, window.timezone))
    return quotes, fallbacks


def _ask(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    llm: object, system: str, stats: ThemeStats, pool: list[Review], limits: PulseSettings
) -> tuple[Review, str] | None:
    """The model's pick, re-asked with the reason up to `limits.retries` times; None if none verifies."""
    payload, keys = reviews_json(pool)
    by_id = {review.review_id: review for review in pool}
    user = f"Theme: {stats.name}. Pick one quote for it.\n{payload}"
    error = None
    for _ in range(1 + limits.retries):
        prompt = user + REASK_SUFFIX.format(error=error) if error else user
        try:
            pick = llm.structured(LLMRequest("quotes", system, prompt, QuotePick))
        except (LLMRefusal, LLMTruncated, LLMSchemaError) as failure:
            error = f"the response failed ({failure})"
            continue
        error = _pick_error(pick, keys, by_id, limits)
        if error is None:
            return by_id[keys[pick.k]], pick.quote.strip(SURROUNDING_QUOTES)
    return None


def _pick_error(
    pick: QuotePick, keys: dict[int, str], by_id: dict[str, Review], limits: PulseSettings
) -> str | None:
    if pick.k not in keys:  # QUO-4
        return f"k={pick.k} is not one of the reviews"
    if not verify_quote(pick.quote, by_id[keys[pick.k]].text, limits.quote_max_words):
        return (
            f"the quote is not a verbatim span of review k={pick.k}, "
            f"or is longer than {limits.quote_max_words} words"
        )
    return None


def _fallback(theme_id: str, pool: list[Review], limits: PulseSettings) -> tuple[Review, str]:
    for review in pool:
        sentence = first_sentence(review.text)
        if len(sentence.split()) >= FALLBACK_MIN_WORDS and verify_quote(
            sentence, review.text, limits.quote_max_words
        ):
            return review, sentence
    raise QuoteUnavailable(theme_id)


def _quote(review: Review, theme_id: str, text: str, timezone: str) -> Quote:
    month = review.posted_at.astimezone(ZoneInfo(timezone)).strftime("%b %Y")  # QUO-10: IST
    return Quote(
        review_id=review.review_id,
        theme_id=theme_id,
        text=text,
        rating=review.rating,
        source=review.source,
        month=month,
    )


def _quotable(review: Review, limits: PulseSettings) -> bool:
    return (
        review.excluded_reason is None
        and not review.is_duplicate_text  # QUO-5
        and review.language in QUOTABLE_LANGUAGES
        and limits.quote_min_words <= review.word_count <= limits.quote_pool_max_words
        and not PLACEHOLDER_RE.search(review.text)  # PII-9: never quote a redaction
    )


def _in_latest_week(review: Review, window: Window) -> bool:
    return iso_week_of(review.posted_at, window.timezone) == window.iso_week
