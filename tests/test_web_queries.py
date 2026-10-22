import os
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest
from web_data import OCT, WINDOW, at, review, taxonomy, write

from pulse.analysis.aggregate import aggregate
from pulse.llm.classify import prompt_version
from pulse.store import Store
from pulse.timeutil import window_bounds_utc
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
