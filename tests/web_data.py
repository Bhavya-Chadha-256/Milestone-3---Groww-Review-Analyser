"""Sample reviews for the dashboard tests (Phase 7): a small store across W42, earlier weeks and before."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factories import make_classification, make_review

from pulse.llm.classify import prompt_version
from pulse.models import Review, Taxonomy, Theme
from pulse.store import Store
from pulse.timeutil import window_for_week

TIMEZONE = "Asia/Kolkata"
WINDOW = window_for_week("2026-W42", 12, TIMEZONE)
OCT = 10


def at(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def review(review_id: str, **overrides: Any) -> Review:
    return make_review(review_id=review_id, text_hash=f"h-{review_id}", **overrides)


def taxonomy() -> Taxonomy:
    themes = [
        Theme(id=theme_id, name=name, definition=name, include_hints=[], exclude_hints=[])
        for theme_id, name in (("withdrawals", "Withdrawals"), ("charges", "Charges"))
    ]
    return Taxonomy(
        version=1, themes=themes, created_from_run="x", created_at=at(OCT, 1), sample_review_ids=[]
    )


SAMPLE = [
    review("r1", source="play_store", rating=1, posted_at=at(OCT, 14)),
    review(
        "r2", source="app_store", rating=5, posted_at=at(OCT, 6), text="Charges are 50 percent higher now"
    ),
    review("r3", source="play_store", rating=2, posted_at=at(9, 10)),
    review("r4", source="play_store", rating=4, posted_at=at(OCT, 13), text="Fees went up 50% this month"),
    review("r5", source="play_store", rating=3, posted_at=at(OCT, 13), excluded_reason="too_short"),
    review("r6", source="play_store", rating=5, posted_at=at(OCT, 12)),
    review("r7", source="app_store", rating=1, posted_at=at(OCT, 15), is_duplicate_text=True),
    review("r8", source="play_store", rating=1, posted_at=at(7, 1)),  # before the window
    review(
        "r9", source="play_store", rating=4, posted_at=datetime(2026, OCT, 11, 19, 0, tzinfo=UTC)
    ),  # 12 Oct 00:30 IST
]
LABELS = {
    "r1": ("withdrawals", "negative"),
    "r2": ("charges", "positive"),
    "r3": ("withdrawals", "mixed"),
    "r4": ("unthemed", "positive"),
    "r7": ("charges", "negative"),
    "r8": ("withdrawals", "negative"),
    "r9": ("withdrawals", "positive"),
}


def write(path: Path, reviews: list[Review] = SAMPLE) -> None:
    store = Store(path)
    store.upsert_reviews(reviews)
    store.save_taxonomy(taxonomy())
    store.save_classifications(
        make_classification(review_id, theme, sentiment=sentiment, prompt_version=prompt_version())
        for review_id, (theme, sentiment) in LABELS.items()
    )
    store.conn.close()
