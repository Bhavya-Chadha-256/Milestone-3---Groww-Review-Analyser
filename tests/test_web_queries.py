import os
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from factories import make_classification, make_review

from pulse.analysis.aggregate import aggregate
from pulse.llm.classify import prompt_version
from pulse.models import Review, Taxonomy, Theme
from pulse.store import Store
from pulse.timeutil import window_bounds_utc, window_for_week
from pulse.web.queries import (
    REVIEW_FIELDS,
    Dataset,
    ReviewFilter,
    data_quality,
    filtered,
    load_dataset,
    rating_distribution,
    review_page,
    summary,
    theme_breakdown,
    weekly_trend,
)

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


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pulse.db"
    write(path)
    return path


@pytest.fixture
def dataset(db_path: Path) -> Dataset:
    return load_dataset(Store.open_readonly(db_path), WINDOW)


def ids(dataset: Dataset, flt: ReviewFilter) -> set[str]:
    return {item.review_id for item, _ in filtered(dataset, flt)}


def test_default_filter_matches_pipeline_aggregates(db_path: Path, dataset: Dataset) -> None:  # E-WEB-1
    store = Store.open_readonly(db_path)
    in_window = store.reviews_between(*window_bounds_utc(WINDOW))
    labels = store.get_classifications([item.review_id for item in in_window], 1, prompt_version())
    expected = aggregate(in_window, labels, taxonomy(), WINDOW).themes
    assert [asdict(stats) for stats in theme_breakdown(dataset, ReviewFilter())] == [
        asdict(stats) for stats in expected
    ]


def test_filters_or_within_a_field_and_across_fields(dataset: Dataset) -> None:
    assert ids(dataset, ReviewFilter(ratings=[1, 2])) == {"r1", "r3", "r7"}
    assert ids(dataset, ReviewFilter(ratings=[1, 2], sources=["app_store"])) == {"r7"}


def test_theme_and_sentiment_filters_drop_unclassified(dataset: Dataset) -> None:
    assert ids(dataset, ReviewFilter(themes=["unthemed"])) == {"r4"}
    positive = ReviewFilter(sentiments=["positive"])
    assert ids(dataset, positive) == {"r2", "r4", "r9"}
    assert data_quality(dataset, positive)["dropped_unclassified"] == 1  # r6


def test_theme_breakdown_ignores_theme_filter(dataset: Dataset) -> None:
    only_charges = theme_breakdown(dataset, ReviewFilter(themes=["charges"]))
    assert [asdict(stats) for stats in only_charges] == [
        asdict(stats) for stats in theme_breakdown(dataset, ReviewFilter())
    ]


def test_query_is_case_insensitive_literal(dataset: Dataset) -> None:
    assert ids(dataset, ReviewFilter(query="50%")) == {"r4"}
    assert ids(dataset, ReviewFilter(query="FEES")) == {"r4"}
    assert ids(dataset, ReviewFilter(query="f.es")) == set()


def test_excluded_reviews_hidden_unless_included(dataset: Dataset) -> None:
    assert "r5" not in ids(dataset, ReviewFilter())
    assert "r5" in ids(dataset, ReviewFilter(include_excluded=True))
    total = summary(dataset, ReviewFilter())["total"]
    assert summary(dataset, ReviewFilter(include_excluded=True))["total"] == total + 1


def test_hide_duplicates_drops_duplicate_text(dataset: Dataset) -> None:
    assert "r7" in ids(dataset, ReviewFilter())
    assert "r7" not in ids(dataset, ReviewFilter(hide_duplicates=True))


def test_date_filter_uses_ist_boundaries(dataset: Dataset) -> None:  # AGG-1
    assert "r9" in ids(dataset, ReviewFilter(start=date(2026, 10, 12), end=date(2026, 10, 18)))
    assert "r9" not in ids(dataset, ReviewFilter(start=date(2026, 10, 5), end=date(2026, 10, 11)))
    week_42 = next(week for week in weekly_trend(dataset, ReviewFilter()) if week["week"] == "2026-W42")
    assert week_42["count"] == 5  # r1, r4, r6, r7, r9


def test_summary_counts_the_eligible_basis(dataset: Dataset) -> None:
    found = summary(dataset, ReviewFilter())
    assert found["total"] == 7 and found["negative_share"] == pytest.approx(3 / 7)
    assert found["change"] is not None  # r8 sits in the previous 12 weeks


def test_empty_result_returns_zeros_not_errors(dataset: Dataset) -> None:
    nothing = ReviewFilter(sources=["app_store"], ratings=[3])
    found = summary(dataset, nothing)
    assert found == {"total": 0, "mean_rating": None, "negative_share": 0.0, "change": None}
    assert review_page(dataset, nothing, "newest", 1, 50) == {"items": [], "total": 0, "page": 1}
    assert all(stats.count == 0 for stats in theme_breakdown(dataset, nothing))
    assert rating_distribution(dataset, nothing) == {
        "play_store": dict.fromkeys(range(1, 6), 0),
        "app_store": dict.fromkeys(range(1, 6), 0),
    }


def test_review_items_carry_only_allowlisted_fields(dataset: Dataset) -> None:  # E-WEB-2
    page = review_page(dataset, ReviewFilter(), "newest", 1, 2)
    assert page["total"] == 7 and [item["review_id"] for item in page["items"]] == ["r7", "r1"]
    assert all(tuple(item) == REVIEW_FIELDS for item in page["items"])
    assert page["items"][0]["posted_on"] == "2026-10-15" and page["items"][0]["theme"] == "charges"
    lowest = review_page(dataset, ReviewFilter(), "lowest_rating", 1, 50)["items"]
    assert [item["rating"] for item in lowest] == sorted(item["rating"] for item in lowest)


def test_dataset_reloads_when_database_changes(db_path: Path) -> None:
    readonly = Store.open_readonly(db_path)
    first = load_dataset(readonly, WINDOW)
    writer = Store(db_path)
    writer.upsert_reviews([review("r10", posted_at=at(OCT, 16))])
    writer.conn.close()
    later = os.stat(db_path).st_mtime + 5
    os.utime(db_path, (later, later))
    second = load_dataset(readonly, WINDOW)
    assert second is not first and len(second.reviews) == len(first.reviews) + 1
    assert load_dataset(readonly, WINDOW) is second


def test_open_readonly_never_creates_or_writes_db(tmp_path: Path, db_path: Path) -> None:
    missing = tmp_path / "absent" / "pulse.db"
    with pytest.raises(FileNotFoundError):
        Store.open_readonly(missing)
    assert not missing.parent.exists()
    before = db_path.read_bytes()
    readonly = Store.open_readonly(db_path)
    load_dataset(readonly, WINDOW)
    with pytest.raises(sqlite3.OperationalError):
        readonly.conn.execute("DELETE FROM reviews")
    assert db_path.read_bytes() == before
