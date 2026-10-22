from datetime import UTC, datetime

import pytest
from factories import make_classification, make_review

from pulse.analysis.aggregate import (
    InsufficientData,
    aggregate,
    rank_top,
    theme_mapping,
    trend_arrow,
    trend_significant,
)
from pulse.models import Classification, Review, Taxonomy, Theme
from pulse.timeutil import window_for_week

LATEST = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)  # in 2026-W42
EARLIER = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def window() -> object:
    return window_for_week("2026-W42", 12, "Asia/Kolkata")


def taxonomy3() -> Taxonomy:
    themes = [
        Theme(id=slug, name=slug.title(), definition=slug, include_hints=[], exclude_hints=[])
        for slug in ("support", "charges", "bugs")
    ]
    return Taxonomy(version=1, themes=themes, created_from_run="r", created_at=LATEST, sample_review_ids=[])


def build(
    spec: list[tuple[str, int, str]], **fields: object
) -> tuple[list[Review], dict[str, Classification]]:
    """spec rows: (theme_id, rating, sentiment); one review per row, all classified."""
    reviews, labels = [], {}
    for index, (theme_id, rating, sentiment) in enumerate(spec):
        review_id = f"{fields.get('prefix', 'r')}{index:04d}"
        reviews.append(
            make_review(review_id=review_id, rating=rating, posted_at=fields.get("posted_at", LATEST))
        )
        labels[review_id] = make_classification(review_id, theme_id, sentiment=sentiment)
    return reviews, labels


def test_counts_shares_and_ratings() -> None:
    reviews, labels = build(
        [("support", 1, "negative")] * 6 + [("charges", 4, "mixed")] * 3 + [("unthemed", 5, "positive")]
    )
    agg = aggregate(reviews, labels, taxonomy3(), window())
    stats = {theme.theme_id: theme for theme in agg.themes}
    assert agg.total == 10 and agg.mean_rating == pytest.approx(2.3)
    assert (stats["support"].count, stats["support"].share, stats["support"].low_rating_share) == (
        6,
        0.6,
        1.0,
    )
    assert stats["charges"].mean_rating == 4.0 and stats["charges"].sentiment == {"mixed": 3}
    assert stats["bugs"].count == 0 and [theme.theme_id for theme in agg.themes] == [
        "support",
        "charges",
        "bugs",
    ]
    assert (agg.unthemed_count, agg.unthemed_share) == (1, 0.1)
    assert stats["support"].sources == {"play_store": 6}


def test_unclassified_reviews_are_excluded_from_total() -> None:  # AGG-10
    reviews, labels = build([("support", 1, "negative")] * 4)
    reviews.append(make_review(review_id="orphan"))
    agg = aggregate(reviews, labels, taxonomy3(), window())
    assert agg.total == 4 and agg.unclassified_count == 1


def test_window_totals_include_excluded_reviews() -> None:  # AGG-14
    reviews, labels = build([("support", 1, "negative")] * 10)
    reviews += [
        make_review(review_id=f"s{index}", rating=5, excluded_reason="too_short") for index in range(40)
    ]
    agg = aggregate(reviews, labels, taxonomy3(), window())
    assert (agg.window_total, agg.total, agg.excluded) == (50, 10, {"too_short": 40})
    assert agg.window_mean_rating == pytest.approx(4.2) and agg.mean_rating == 1.0
    assert agg.unclassified_count == 0 and agg.sources == {"play_store": 50}


def test_actionable_unthemed_share_ignores_positive_reviews() -> None:  # LLM-16
    spec = (
        [("unthemed", 5, "positive")] * 30
        + [("support", 1, "negative")] * 60
        + [("unthemed", 2, "negative")] * 10
    )
    reviews, labels = build(spec)
    agg = aggregate(reviews, labels, taxonomy3(), window())
    assert agg.unthemed_share == pytest.approx(0.40)
    assert agg.actionable_unthemed_share == pytest.approx(10 / 70)


def test_actionable_unthemed_share_is_zero_without_actionable_reviews() -> None:
    reviews, labels = build([("unthemed", 5, "positive")] * 3)
    assert aggregate(reviews, labels, taxonomy3(), window()).actionable_unthemed_share == 0.0


def test_rank_top_breaks_ties_by_low_rating_share() -> None:  # AGG-5
    spec = [("support", 4, "mixed")] * 3 + [("charges", 1, "negative")] * 3 + [("bugs", 1, "negative")] * 2
    reviews, labels = build(spec)
    top = rank_top(aggregate(reviews, labels, taxonomy3(), window()))
    assert [theme.theme_id for theme in top] == ["charges", "support", "bugs"]


def test_rank_top_raises_when_fewer_than_three_populated_themes() -> None:  # AGG-3
    reviews, labels = build([("support", 1, "negative")] * 5 + [("charges", 1, "negative")] * 5)
    with pytest.raises(InsufficientData, match="only 2 themes have reviews; need 3"):
        rank_top(aggregate(reviews, labels, taxonomy3(), window()))


def test_trend_is_none_when_latest_week_empty() -> None:  # AGG-4
    reviews, labels = build([("support", 1, "negative")] * 5, posted_at=EARLIER)
    agg = aggregate(reviews, labels, taxonomy3(), window())
    assert agg.latest_week_total == 0
    assert all(theme.latest_week_share is None and theme.trend_pts is None for theme in agg.themes)


def test_trend_compares_latest_week_with_window() -> None:
    earlier, earlier_labels = build(
        [("support", 1, "negative")] * 50 + [("charges", 1, "negative")] * 50, prefix="e", posted_at=EARLIER
    )
    latest, latest_labels = build(
        [("support", 1, "negative")] * 90 + [("charges", 1, "negative")] * 10, prefix="l"
    )
    agg = aggregate(earlier + latest, earlier_labels | latest_labels, taxonomy3(), window())
    support = next(theme for theme in agg.themes if theme.theme_id == "support")
    assert agg.latest_week_total == 100 and support.latest_week_share == 0.9
    assert support.trend_pts == 20 and support.trend_significant


@pytest.mark.parametrize("case", [(6, True, "↑"), (-6, True, "↓"), (6, False, "→"), (None, False, "")])
def test_trend_arrow(case: tuple[int | None, bool, str]) -> None:
    pts, significant, arrow = case
    assert trend_arrow(pts, significant) == arrow


def test_trend_needs_two_standard_errors() -> None:  # AGG-13: n_latest=135, share 0.15 → 2·SE ≈ 6.1 pts
    assert not trend_significant(0.15, 0.19, 135, 2.0)
    assert trend_significant(0.15, 0.23, 135, 2.0)
    assert not trend_significant(0.15, 0.15, 135, 2.0)


def test_broad_theme_is_warned() -> None:  # AGG-15
    spec = (
        [("support", 1, "negative")] * 45 + [("charges", 1, "negative")] * 30 + [("bugs", 1, "negative")] * 25
    )
    reviews, labels = build(spec)
    agg = aggregate(reviews, labels, taxonomy3(), window(), broad_theme_warn=0.40)
    assert agg.warnings == ["broad_theme:support"]


def test_duplicate_share_reported() -> None:  # AGG-8
    reviews, labels = build([("support", 1, "negative")] * 100)
    reviews = [
        review.model_copy(update={"is_duplicate_text": index < 30}) for index, review in enumerate(reviews)
    ]
    agg = aggregate(reviews, labels, taxonomy3(), window())
    assert agg.duplicate_share == pytest.approx(0.30) and agg.total == 100


def test_low_sample_flag() -> None:  # AGG-7
    reviews, labels = build([("support", 1, "negative")] * 3 + [("charges", 1, "negative")] * 12)
    stats = {theme.theme_id: theme for theme in aggregate(reviews, labels, taxonomy3(), window()).themes}
    assert stats["support"].low_sample and not stats["charges"].low_sample


def test_theme_mapping_uses_majority() -> None:  # AGG-11
    old = {f"r{index}": make_classification(f"r{index}", "money") for index in range(5)}
    old |= {f"q{index}": make_classification(f"q{index}", "app") for index in range(2)}
    new = {
        f"r{index}": make_classification(f"r{index}", "withdrawals" if index < 4 else "charges")
        for index in range(5)
    }
    new |= {f"q{index}": make_classification(f"q{index}", "bugs", taxonomy_version=2) for index in range(2)}
    assert theme_mapping(old, new) == {"app": "bugs", "money": "withdrawals"}
