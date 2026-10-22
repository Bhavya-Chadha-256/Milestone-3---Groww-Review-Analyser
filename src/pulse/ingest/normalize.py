"""Turn RawReviews into PII-free Reviews, and mark theme eligibility and duplicates (architecture.md §3.2)."""

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from pathlib import Path

from pulse.models import Exclusion, Language, RawReview, Review
from pulse.privacy.scrub import Scrubber

HINGLISH_MARKERS = frozenset(
    "hai nahi nahin kya kyu paisa paise kar karo raha rahi bhi mera mere bhai kab ho gaya nhi".split()
)
DEVANAGARI = range(0x0900, 0x0980)


def make_review_id(source: str, native_id: str) -> str:
    return hashlib.sha256(f"{source}:{native_id}".encode()).hexdigest()[:16]


def count_words(text: str) -> int:
    """Whitespace tokens with at least one letter or digit, so emoji and punctuation don't count."""
    return sum(1 for token in text.split() if any(char.isalnum() for char in token))


def detect_language(text: str) -> Language:
    # Letters include combining marks, so scripts that use vowel signs are counted in full.
    letters = [char for char in text if unicodedata.category(char)[0] in "LM"]
    if not letters:
        return "other"
    if sum(ord(char) in DEVANAGARI for char in letters) / len(letters) >= 0.3:
        return "hi"
    latin = sum(char.isascii() for char in letters)
    if latin < len(letters) / 2:
        return "other"
    markers = sum(token in HINGLISH_MARKERS for token in re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if markers >= 2 else "en"


def normalize(raw: RawReview, scrubber: Scrubber) -> Review:
    """Scrub title and body (PII-4), then derive the hash, word count and language from the scrubbed text."""
    title = scrubber.scrub(raw.title).text if raw.title else None
    text = scrubber.scrub(raw.text).text
    return Review(
        review_id=make_review_id(raw.source, raw.native_id),
        source=raw.source,
        rating=raw.rating,
        title=title,
        text=text,
        posted_at=raw.posted_at,
        app_version=raw.app_version,
        language=detect_language(text),
        word_count=count_words(text),
        text_hash=hashlib.sha256(f"{title or ''}\n{text}".encode()).hexdigest()[:16],
    )


def exclusion_for(review: Review, min_words: int, languages: Iterable[str]) -> Exclusion | None:
    if review.language not in languages:
        return "unsupported_language"
    if review.word_count < min_words:
        return "too_short"
    return None


def mark_exclusions(
    reviews: Iterable[Review], min_words: int, languages: Iterable[str]
) -> tuple[list[Review], Counter[str]]:
    """Every review, with `excluded_reason` set (or cleared) for theme analysis, plus counts by reason."""
    allowed = set(languages)
    marked = [
        review.model_copy(update={"excluded_reason": exclusion_for(review, min_words, allowed)})
        for review in reviews
    ]
    return marked, Counter(review.excluded_reason for review in marked if review.excluded_reason)


def mark_duplicates(reviews: Iterable[Review]) -> list[Review]:
    """Flag every review whose text repeats an earlier review's text (case and outer whitespace ignored)."""
    reviews = list(reviews)
    first_seen: dict[str, str] = {}
    for review in sorted(reviews, key=lambda item: (item.posted_at, item.review_id)):
        first_seen.setdefault(review.text.strip().lower(), review.review_id)
    return [
        review.model_copy(
            update={"is_duplicate_text": first_seen[review.text.strip().lower()] != review.review_id}
        )
        for review in reviews
    ]


def purge_raw(raw_dir: Path, days: int, now: datetime, *, read_paths: Collection[Path]) -> list[Path]:
    """Delete raw files that were read this run and last modified over `days` ago (PII-11).

    Files that were never read (unmapped, unreadable, missing columns) are kept, so an export is never
    lost before it has been ingested.
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return []
    cutoff = (now - timedelta(days=days)).timestamp()
    read = {Path(path).resolve() for path in read_paths}
    purged = [
        path
        for path in sorted(raw_dir.iterdir())
        if path.is_file() and path.resolve() in read and path.stat().st_mtime < cutoff
    ]
    for path in purged:
        path.unlink()
    return purged
