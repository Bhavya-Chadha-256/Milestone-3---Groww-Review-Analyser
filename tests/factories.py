"""Builders for shared models, so tests state only the fields they care about (Task 2.1)."""

from datetime import UTC, datetime
from typing import Any

from pulse.models import Classification, Review


def make_review(**overrides: Any) -> Review:
    fields: dict[str, Any] = {
        "review_id": "r1",
        "source": "play_store",
        "rating": 3,
        "title": None,
        "text": "The app keeps crashing whenever I open my portfolio page",
        "posted_at": datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        "app_version": "18.22.0",
        "language": "en",
        "word_count": 10,
        "text_hash": "h1",
    }
    return Review(**(fields | overrides))


def make_classification(review_id: str, theme_id: str, **overrides: Any) -> Classification:
    fields: dict[str, Any] = {
        "review_id": review_id,
        "theme_id": theme_id,
        "sentiment": "negative",
        "taxonomy_version": 1,
        "prompt_version": "classify-v1",
    }
    return Classification(**(fields | overrides))
