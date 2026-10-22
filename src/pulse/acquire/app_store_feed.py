"""Client for Apple's public customer-reviews RSS/JSON feed (architecture.md §3.1).

The feed needs no login but is an undocumented legacy endpoint: at most 10 pages of 50 reviews,
and pages intermittently come back empty (HTTP 200, no `entry`). So empty pages are retried with
backoff, then skipped while paging continues (edgecase ING-16, ING-19, ING-20).
Author fields are never read.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError

from pulse.models import RawReview

FEED_URL = (
    "https://itunes.apple.com/{storefront}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
)
USER_AGENT = "groww-review-pulse/0.1 (portfolio project; public feed)"


@dataclass
class FetchReport:
    pages_requested: int = 0
    pages_with_entries: int = 0
    pages_empty: int = 0
    retries: int = 0
    invalid_entries: int = 0
    reviews: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    cap_reached: bool = False
    warnings: list[str] = field(default_factory=list)


class AppStoreFeedClient:
    def __init__(  # craftsman-ignore: WARN-PY001 (tunables mirror the plan's interface; all keyword-optional)
        self,
        app_id: str,
        storefront: str = "in",
        http: httpx.Client | None = None,
        max_pages: int = 10,
        attempts: int = 3,
        backoff_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.app_id = app_id
        self.storefront = storefront
        self.http = http or httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT})
        self.max_pages = max_pages
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def fetch(self, since: datetime) -> tuple[list[RawReview], FetchReport]:
        report = FetchReport()
        collected: dict[str, RawReview] = {}
        for page in range(1, self.max_pages + 1):
            report.pages_requested += 1
            entries = self._fetch_page(page, report)
            if not entries:
                report.pages_empty += 1
                continue
            report.pages_with_entries += 1
            page_reviews = [review for entry in entries if (review := self._parse(entry, report))]
            if self._collect(page_reviews, since, collected):
                break
            report.cap_reached = page == self.max_pages
        reviews = list(collected.values())
        self._summarise(reviews, report)
        return reviews, report

    @staticmethod
    def _collect(page_reviews: list[RawReview], since: datetime, collected: dict[str, RawReview]) -> bool:
        """Keep in-window reviews (first copy wins); return True once the page reaches past `since`."""
        for review in page_reviews:
            if review.posted_at >= since:
                collected.setdefault(review.native_id, review)
        return bool(page_reviews) and min(review.posted_at for review in page_reviews) < since

    def _fetch_page(self, page: int, report: FetchReport) -> list[dict[str, Any]]:
        url = FEED_URL.format(storefront=self.storefront, page=page, app_id=self.app_id)
        for attempt in range(1, self.attempts + 1):
            entries = self._entries_or_empty(url)
            if entries:
                return entries
            if attempt < self.attempts:
                report.retries += 1
                self.sleep(self.backoff_seconds * attempt)
        return []

    def _entries_or_empty(self, url: str) -> list[dict[str, Any]]:
        try:
            response = self.http.get(url)
            if response.status_code != 200:
                return []
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        feed = body.get("feed") if isinstance(body, dict) else None
        entry = feed.get("entry") if isinstance(feed, dict) else None
        if isinstance(entry, dict):
            return [entry]
        return entry if isinstance(entry, list) else []

    @staticmethod
    def _parse(entry: dict[str, Any], report: FetchReport) -> RawReview | None:
        if "im:rating" not in entry:  # e.g. an app-metadata entry
            return None
        try:
            return RawReview(
                source="app_store",
                native_id=_label(entry, "id"),
                rating=int(_label(entry, "im:rating")),
                title=_label(entry, "title"),
                text=_label(entry, "content") or "",
                posted_at=datetime.fromisoformat(_label(entry, "updated")),
                app_version=_label(entry, "im:version"),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            report.invalid_entries += 1
            return None

    @staticmethod
    def _summarise(reviews: list[RawReview], report: FetchReport) -> None:
        report.reviews = len(reviews)
        if reviews:
            report.oldest = min(review.posted_at for review in reviews)
            report.newest = max(review.posted_at for review in reviews)
        if report.pages_empty:
            report.warnings.append(f"app_store_feed_empty_pages:{report.pages_empty}")
        if report.cap_reached:
            report.warnings.append("app_store_feed_cap_reached")
        if report.invalid_entries:
            report.warnings.append(f"app_store_feed_invalid_entries:{report.invalid_entries}")


def _label(entry: dict[str, Any], key: str) -> Any:
    """`entry[key]["label"]`, or None when the field is missing or not the expected shape."""
    value = entry.get(key)
    return value.get("label") if isinstance(value, dict) else None
