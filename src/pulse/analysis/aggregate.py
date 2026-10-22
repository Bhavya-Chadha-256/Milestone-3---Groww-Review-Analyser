"""Theme statistics, trend and ranking, computed in code (architecture.md §3.5, Task 3.4).

Two bases (D1): window totals cover every stored review; theme statistics cover classified eligible reviews.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from pulse.models import UNTHEMED, Classification, Review, Taxonomy
from pulse.timeutil import Window, iso_week_of

ACTIONABLE_SENTIMENTS = frozenset({"negative", "mixed"})

Labelled = list[tuple[Review, Classification]]


class InsufficientData(Exception):
    pass


@dataclass
class ThemeStats:
    theme_id: str
    name: str
    count: int
    share: float
    mean_rating: float | None
    low_rating_share: float
    sentiment: dict[str, int]
    latest_week_share: float | None
    trend_pts: int | None
    trend_significant: bool
    sources: dict[str, int]
    low_sample: bool


@dataclass
class Aggregates:
    window: Window
    window_total: int
    window_mean_rating: float | None
    excluded: dict[str, int]
    total: int
    mean_rating: float | None
    sources: dict[str, int]
    unthemed_count: int
    unthemed_share: float
    actionable_unthemed_share: float
    latest_week_total: int
    duplicate_share: float
    unclassified_count: int
    themes: list[ThemeStats]
    warnings: list[str] = field(default_factory=list)


def trend_significant(share: float, latest_share: float, latest_total: int, trend_z: float) -> bool:
    """A change counts only at ≥ `trend_z` standard errors of the latest week's share (D5, AGG-13)."""
    if latest_total == 0 or round((latest_share - share) * 100) == 0:
        return False
    standard_error = math.sqrt(share * (1 - share) / latest_total)
    return abs(latest_share - share) >= trend_z * standard_error


def trend_arrow(pts: int | None, significant: bool) -> str:
    if pts is None:
        return ""
    if not significant:
        return "→"
    return "↑" if pts > 0 else "↓"


def aggregate(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    reviews: Sequence[Review],
    cls_by_id: dict[str, Classification],
    taxonomy: Taxonomy,
    window: Window,
    *,
    min_theme_reviews: int = 10,
    trend_z: float = 2.0,
    broad_theme_warn: float = 0.40,
) -> Aggregates:
    eligible = [review for review in reviews if review.excluded_reason is None]
    classified = [
        (review, cls_by_id[review.review_id]) for review in eligible if review.review_id in cls_by_id
    ]
    latest = [(review, label) for review, label in classified if _in_latest_week(review, window)]
    themes = [
        _theme_stats(theme.id, theme.name, classified, latest, min_theme_reviews, trend_z)
        for theme in taxonomy.themes
    ]
    return Aggregates(
        window=window,
        **_window_totals(reviews),
        **_theme_totals(classified, latest, unclassified=len(eligible) - len(classified)),
        themes=themes,
        warnings=[f"broad_theme:{stats.theme_id}" for stats in themes if stats.share > broad_theme_warn],
    )


def rank_top(agg: Aggregates, n: int = 3) -> list[ThemeStats]:
    populated = [stats for stats in agg.themes if stats.count > 0]
    if len(populated) < n:
        raise InsufficientData(f"only {len(populated)} themes have reviews; need {n}")
    return sorted(populated, key=lambda stats: (-stats.count, -stats.low_rating_share, stats.theme_id))[:n]


def theme_mapping(old: dict[str, Classification], new: dict[str, Classification]) -> dict[str, str]:
    """Each old theme → the new theme most of its reviews moved to (ties broken by new theme ID, AGG-11)."""
    moved: dict[str, Counter[str]] = {}
    for review_id, label in old.items():
        if label.theme_id != UNTHEMED and review_id in new:
            moved.setdefault(label.theme_id, Counter())[new[review_id].theme_id] += 1
    return {
        old_theme: min(targets.items(), key=lambda item: (-item[1], item[0]))[0]
        for old_theme, targets in sorted(moved.items())
    }


def _window_totals(reviews: Sequence[Review]) -> dict[str, Any]:
    """Every stored review in the window, excluded ones included (D1, AGG-14)."""
    return {
        "window_total": len(reviews),
        "window_mean_rating": _mean([review.rating for review in reviews]),
        "excluded": dict(Counter(review.excluded_reason for review in reviews if review.excluded_reason)),
        "sources": dict(Counter(review.source for review in reviews)),
    }


def _theme_totals(classified: Labelled, latest: Labelled, *, unclassified: int) -> dict[str, Any]:
    """Classified eligible reviews only; the basis of every theme share."""
    total = len(classified)
    unthemed = sum(label.theme_id == UNTHEMED for _, label in classified)
    actionable = [label for _, label in classified if label.sentiment in ACTIONABLE_SENTIMENTS]
    return {
        "total": total,
        "mean_rating": _mean([review.rating for review, _ in classified]),
        "unthemed_count": unthemed,
        "unthemed_share": _share(unthemed, total),
        "actionable_unthemed_share": _share(
            sum(label.theme_id == UNTHEMED for label in actionable), len(actionable)
        ),
        "latest_week_total": len(latest),
        "duplicate_share": _share(sum(review.is_duplicate_text for review, _ in classified), total),
        "unclassified_count": unclassified,
    }


def _theme_stats(  # craftsman-ignore: WARN-PY001, PY003 (hint on closing line)
    theme_id: str,
    name: str,
    classified: Labelled,
    latest: Labelled,
    min_theme_reviews: int,
    trend_z: float,
) -> ThemeStats:
    members = [review for review, label in classified if label.theme_id == theme_id]
    share = _share(len(members), len(classified))
    latest_share = (
        _share(sum(label.theme_id == theme_id for _, label in latest), len(latest)) if latest else None
    )
    pts = round((latest_share - share) * 100) if latest_share is not None else None
    return ThemeStats(
        theme_id=theme_id,
        name=name,
        count=len(members),
        share=share,
        mean_rating=_mean([review.rating for review in members]),
        low_rating_share=_share(sum(review.rating <= 2 for review in members), len(members)),
        sentiment=dict(Counter(label.sentiment for _, label in classified if label.theme_id == theme_id)),
        latest_week_share=latest_share,
        trend_pts=pts,
        trend_significant=latest_share is not None
        and trend_significant(share, latest_share, len(latest), trend_z),
        sources=dict(Counter(review.source for review in members)),
        low_sample=len(members) < min_theme_reviews,
    )


def _in_latest_week(review: Review, window: Window) -> bool:
    return iso_week_of(review.posted_at, window.timezone) == window.iso_week


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _mean(values: list[int]) -> float | None:
    return mean(values) if values else None
