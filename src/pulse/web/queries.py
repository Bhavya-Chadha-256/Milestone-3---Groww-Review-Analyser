"""Read-only queries behind the dashboard (Task 7.1, architecture.md §16).

Every number comes from the stored reviews and classifications. Theme statistics reuse the pipeline's
`aggregate()`, so with the default filter the dashboard equals `aggregates.json` (E-WEB-1). Review text
is always the scrubbed `Review.text`, and review items carry only `REVIEW_FIELDS` (E-WEB-2).
"""

import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from annotated_types import Ge as AtLeast
from annotated_types import Le as AtMost
from annotated_types import MaxLen

from pulse.analysis.aggregate import ThemeStats, aggregate
from pulse.config import StrictModel
from pulse.llm.classify import prompt_version
from pulse.models import UNTHEMED, Classification, Language, Review, Source, Taxonomy
from pulse.store import Store
from pulse.timeutil import Window, iso_week_label

REVIEW_FIELDS = (
    "review_id",
    "rating",
    "source",
    "posted_on",
    "app_version",
    "language",
    "theme",
    "sentiment",
    "title",
    "text",
)
SORTS = ("newest", "lowest_rating", "highest_rating", "longest")
SOURCES: tuple[Source, ...] = ("play_store", "app_store")
Sentiment = Literal["negative", "mixed", "positive"]
Rating = Annotated[int, AtLeast(1), AtMost(5)]
Row = tuple[Review, Classification | None]


class ReviewFilter(StrictModel):
    """An empty list means "all". Values within a field combine with OR; fields combine with AND."""

    start: date | None = None  # IST dates, inclusive; default: the selected week's window
    end: date | None = None
    sources: list[Source] = []
    ratings: list[Rating] = []
    themes: list[str] = []  # theme IDs or "unthemed"
    sentiments: list[Sentiment] = []
    languages: list[Language] = []
    app_versions: list[str] = []
    query: Annotated[str, MaxLen(100)] | None = None
    hide_duplicates: bool = False
    include_excluded: bool = False


@dataclass(frozen=True)
class Dataset:
    reviews: list[Review]  # every stored review, oldest first
    labels: dict[str, Classification]  # under the latest taxonomy and the current classify prompt
    taxonomy: Taxonomy | None
    window: Window
    local_dates: dict[str, date] = field(repr=False)  # review_id → IST posting date
    min_theme_reviews: int = 10
    trend_z: float = 2.0


_CACHE: dict[Path, tuple[tuple[Any, ...], Dataset]] = {}
_CACHE_LOCK = threading.Lock()


def load_dataset(  # craftsman-ignore: PY003 (hint on closing line)
    store: Store, window: Window, *, min_theme_reviews: int = 10, trend_z: float = 2.0
) -> Dataset:
    """The stored data, cached until the database file changes (a pipeline run writes it)."""
    key = (store.path.stat().st_mtime_ns, window, min_theme_reviews, trend_z)
    with _CACHE_LOCK:
        cached = _CACHE.get(store.path)
        if cached and cached[0] == key:
            return cached[1]
        dataset = _load(store, window, min_theme_reviews, trend_z)
        _CACHE[store.path] = (key, dataset)
        return dataset


def _load(store: Store, window: Window, min_theme_reviews: int, trend_z: float) -> Dataset:
    reviews = store.all_reviews()
    taxonomy = store.latest_taxonomy()
    labels: dict[str, Classification] = {}
    if taxonomy is not None:
        eligible = [review.review_id for review in reviews if review.excluded_reason is None]
        labels = store.get_classifications(eligible, taxonomy.version, prompt_version())
    zone = ZoneInfo(window.timezone)
    return Dataset(
        reviews=reviews,
        labels=labels,
        taxonomy=taxonomy,
        window=window,
        local_dates={review.review_id: review.posted_at.astimezone(zone).date() for review in reviews},
        min_theme_reviews=min_theme_reviews,
        trend_z=trend_z,
    )


def period(dataset: Dataset, flt: ReviewFilter) -> tuple[date, date]:
    return flt.start or dataset.window.start, flt.end or dataset.window.end


def filtered(dataset: Dataset, flt: ReviewFilter) -> list[Row]:
    start, end = period(dataset, flt)
    needle = flt.query.casefold() if flt.query else None
    rows: list[Row] = []
    for review in dataset.reviews:
        label = dataset.labels.get(review.review_id)
        if start <= dataset.local_dates[review.review_id] <= end and _keeps(review, label, flt, needle):
            rows.append((review, label))
    return rows


def _keeps(review: Review, label: Classification | None, flt: ReviewFilter, needle: str | None) -> bool:
    checks = (
        flt.include_excluded or review.excluded_reason is None,
        not (flt.hide_duplicates and review.is_duplicate_text),
        not flt.sources or review.source in flt.sources,
        not flt.ratings or review.rating in flt.ratings,
        not flt.languages or review.language in flt.languages,
        not flt.app_versions or review.app_version in flt.app_versions,
        not flt.themes or (label is not None and label.theme_id in flt.themes),
        not flt.sentiments or (label is not None and label.sentiment in flt.sentiments),
        needle is None or needle in f"{review.title or ''}\n{review.text}".casefold(),  # literal, no regex
    )
    return all(checks)


def summary(dataset: Dataset, flt: ReviewFilter) -> dict[str, Any]:
    """Totals for the filter, and the change against the previous period of the same length."""
    start, end = period(dataset, flt)
    days = timedelta(days=(end - start).days + 1)
    current = _totals(filtered(dataset, flt))
    previous = _totals(
        filtered(dataset, flt.model_copy(update={"start": start - days, "end": start - timedelta(1)}))
    )
    change = None
    if previous["total"]:
        change = {
            "total_pct": (current["total"] - previous["total"]) / previous["total"],
            "mean_rating": _difference(current["mean_rating"], previous["mean_rating"]),
            "negative_share_pts": (current["negative_share"] - previous["negative_share"]) * 100,
        }
    return current | {"change": change}


def _totals(rows: list[Row]) -> dict[str, Any]:
    ratings = [review.rating for review, _ in rows]
    return {
        "total": len(ratings),
        "mean_rating": mean(ratings) if ratings else None,
        "negative_share": sum(rating <= 2 for rating in ratings) / len(ratings) if ratings else 0.0,
    }


def _difference(current: float | None, previous: float | None) -> float | None:
    return current - previous if current is not None and previous is not None else None


def theme_breakdown(dataset: Dataset, flt: ReviewFilter) -> list[ThemeStats]:
    """`aggregate()` over the filtered reviews. The theme filter is ignored, so every theme stays visible."""
    if dataset.taxonomy is None:
        return []
    rows = filtered(dataset, flt.model_copy(update={"themes": []}))
    labels = {review.review_id: label for review, label in rows if label is not None}
    agg = aggregate(
        [review for review, _ in rows],
        labels,
        dataset.taxonomy,
        _window(dataset, flt),
        min_theme_reviews=dataset.min_theme_reviews,
        trend_z=dataset.trend_z,
    )
    return agg.themes


def unthemed_row(dataset: Dataset, flt: ReviewFilter) -> dict[str, Any]:
    """The "Unthemed" line under the themes, on the same basis (classified reviews; theme filter ignored)."""
    rows = filtered(dataset, flt.model_copy(update={"themes": []}))
    labelled = [(review, label) for review, label in rows if label is not None]
    members = [(review, label) for review, label in labelled if label.theme_id == UNTHEMED]
    ratings = [review.rating for review, _ in members]
    return {
        "theme_id": UNTHEMED,
        "name": "Unthemed",
        "count": len(members),
        "share": len(members) / len(labelled) if labelled else 0.0,
        "mean_rating": mean(ratings) if ratings else None,
        "sentiment": dict(Counter(label.sentiment for _, label in members)),
        "trend_pts": None,
        "arrow": "",
    }


def _window(dataset: Dataset, flt: ReviewFilter) -> Window:
    start, end = period(dataset, flt)
    return Window(start=start, end=end, iso_week=iso_week_label(end), timezone=dataset.window.timezone)


def weekly_trend(dataset: Dataset, flt: ReviewFilter) -> list[dict[str, Any]]:
    """Per ISO week (IST) in the period: the count, mean rating and each theme's share of labelled reviews."""
    start, end = period(dataset, flt)
    by_week: dict[str, list[Row]] = {}
    for review, label in filtered(dataset, flt):
        by_week.setdefault(iso_week_label(dataset.local_dates[review.review_id]), []).append((review, label))
    weeks = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        rows = by_week.get(iso_week_label(monday), [])
        ratings = [review.rating for review, _ in rows]
        themes = Counter(label.theme_id for _, label in rows if label is not None)
        labelled = sum(themes.values())
        weeks.append(
            {
                "week": iso_week_label(monday),
                "start": monday.isoformat(),
                "count": len(rows),
                "mean_rating": mean(ratings) if ratings else None,
                "themes": {theme: count / labelled for theme, count in sorted(themes.items())},
            }
        )
        monday += timedelta(days=7)
    return weeks


def rating_distribution(dataset: Dataset, flt: ReviewFilter) -> dict[str, dict[int, int]]:
    counts = {source: dict.fromkeys(range(1, 6), 0) for source in SOURCES}
    for review, _ in filtered(dataset, flt):
        counts[review.source][review.rating] += 1
    return counts


def review_page(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    dataset: Dataset, flt: ReviewFilter, sort: str, page: int, page_size: int
) -> dict[str, Any]:
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    rows = sorted(filtered(dataset, flt), key=_SORT_KEYS[sort])
    first = (page - 1) * page_size
    items = [_item(dataset, review, label) for review, label in rows[first : first + page_size]]
    return {"items": items, "total": len(rows), "page": page}


_SORT_KEYS = {
    "newest": lambda row: (-row[0].posted_at.timestamp(), row[0].review_id),
    "lowest_rating": lambda row: (row[0].rating, -row[0].posted_at.timestamp(), row[0].review_id),
    "highest_rating": lambda row: (-row[0].rating, -row[0].posted_at.timestamp(), row[0].review_id),
    "longest": lambda row: (-row[0].word_count, -row[0].posted_at.timestamp(), row[0].review_id),
}


def _item(dataset: Dataset, review: Review, label: Classification | None) -> dict[str, Any]:
    """Exactly `REVIEW_FIELDS`, in order; the text is the scrubbed text (E-WEB-2)."""
    return {
        "review_id": review.review_id,
        "rating": review.rating,
        "source": review.source,
        "posted_on": dataset.local_dates[review.review_id].isoformat(),
        "app_version": review.app_version,
        "language": review.language,
        "theme": label.theme_id if label else None,
        "sentiment": label.sentiment if label else None,
        "title": review.title,
        "text": review.text,
    }


def data_quality(dataset: Dataset, flt: ReviewFilter) -> dict[str, Any]:
    """What the current view leaves out: excluded reviews by reason, duplicates and unclassified ones."""
    scope = flt.model_copy(
        update={"include_excluded": True, "hide_duplicates": False, "themes": [], "sentiments": []}
    )
    rows = filtered(dataset, scope)
    unclassified = sum(review.excluded_reason is None and label is None for review, label in rows)
    return {
        "excluded": dict(Counter(review.excluded_reason for review, _ in rows if review.excluded_reason)),
        "duplicates": sum(review.is_duplicate_text for review, _ in rows),
        "unclassified": unclassified,
        "dropped_unclassified": unclassified if flt.themes or flt.sentiments else 0,
    }
