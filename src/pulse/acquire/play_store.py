"""Play Store reviews through the `google-play-scraper` library (architecture.md §3.1, Task 1.4).

To keep the load small, pages are fetched newest first with a pause between them, paging stops at
the window start, and `max_reviews` caps the total (edgecase ING-27..ING-29).
Author name, avatar and developer replies are never read.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from pulse.acquire.app_store_feed import FetchReport
from pulse.models import RawReview

PageFetcher = Callable[[Any], tuple[list[dict[str, Any]], Any]]
"""Takes a continuation token (None for the first page); returns the page and the next token."""


class PlayStoreClient:
    def __init__(  # craftsman-ignore: WARN-PY001 (tunables mirror the plan's interface; all keyword-optional)
        self,
        package: str,
        lang: str = "en",
        country: str = "in",
        page_size: int = 200,
        max_reviews: int = 20000,
        attempts: int = 3,
        backoff_seconds: float = 2.0,
        pause_seconds: float = 1.0,
        fetch_page: PageFetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.package = package
        self.lang = lang
        self.country = country
        self.page_size = page_size
        self.max_reviews = max_reviews
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.pause_seconds = pause_seconds
        self.fetch_page = fetch_page or self._scraper_page
        self.sleep = sleep

    def fetch(self, since: datetime) -> tuple[list[RawReview], FetchReport]:
        report = FetchReport()
        collected: dict[str, RawReview] = {}
        token: Any = None
        while True:
            if report.pages_requested:
                self.sleep(self.pause_seconds)
            report.pages_requested += 1
            page = self._fetch_with_retries(token, report)
            if page is None:
                report.warnings.append("play_store_fetch_failed")
                break
            entries, token = page
            report.pages_with_entries += 1
            page_reviews = [review for entry in entries if (review := _parse(entry, report))]
            if self._collect(page_reviews, since, collected) or _token_value(token) is None:
                break
            if len(collected) >= self.max_reviews:
                report.cap_reached = True
                break
        reviews = list(collected.values())[: self.max_reviews]
        _summarise(reviews, report)
        return reviews, report

    def _collect(
        self, page_reviews: list[RawReview], since: datetime, collected: dict[str, RawReview]
    ) -> bool:
        """Keep in-window reviews (first copy wins); return True once the page reaches past `since`."""
        for review in page_reviews:
            if review.posted_at >= since and len(collected) < self.max_reviews:
                collected.setdefault(review.native_id, review)
        return bool(page_reviews) and min(review.posted_at for review in page_reviews) < since

    def _fetch_with_retries(self, token: Any, report: FetchReport) -> tuple[list[dict[str, Any]], Any] | None:
        """A page with entries, or None after `attempts` failures.

        google-play-scraper swallows its own errors and returns an empty page instead, so an empty page
        before the window start counts as a failure and is retried with the same token (ING-29).
        """
        empty = False
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                report.retries += 1
                self.sleep(self.backoff_seconds * (attempt - 1))
            try:
                entries, next_token = self.fetch_page(token)
            except Exception:  # the scraper raises many types (network, parsing); every one is retried
                continue
            if entries:
                return entries, next_token
            empty = True
        report.pages_empty += empty
        return None

    def _scraper_page(self, token: Any) -> tuple[list[dict[str, Any]], Any]:
        from google_play_scraper import Sort, reviews

        return reviews(
            self.package,
            lang=self.lang,
            country=self.country,
            sort=Sort.NEWEST,
            count=self.page_size,
            continuation_token=token,
        )


def _token_value(token: Any) -> Any:
    """The raw token; the library wraps it in a _ContinuationToken whose `.token` is None at the end."""
    return getattr(token, "token", token)


def _parse(entry: dict[str, Any], report: FetchReport) -> RawReview | None:
    try:
        return RawReview(
            source="play_store",
            native_id=entry["reviewId"],
            rating=int(entry["score"]),
            title=None,
            text=entry.get("content") or "",
            posted_at=_to_utc(entry["at"]),
            app_version=entry.get("reviewCreatedVersion") or entry.get("appVersion"),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        report.invalid_entries += 1
        return None


def _to_utc(value: datetime) -> datetime:
    """The library returns naive local-time datetimes (`fromtimestamp`); astimezone reads them as local."""
    if not isinstance(value, datetime):
        raise TypeError("posted_at must be a datetime")
    return value.astimezone(UTC)


def _summarise(reviews: list[RawReview], report: FetchReport) -> None:
    report.reviews = len(reviews)
    if reviews:
        report.oldest = min(review.posted_at for review in reviews)
        report.newest = max(review.posted_at for review in reviews)
    if report.cap_reached:
        report.warnings.append("play_store_cap_reached")
    if report.invalid_entries:
        report.warnings.append(f"play_store_invalid_entries:{report.invalid_entries}")
