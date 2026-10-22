import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from pulse.acquire.app_store_feed import AppStoreFeedClient

FEED_FIXTURES = Path(__file__).parent / "fixtures" / "app_store"
SINCE = datetime(2026, 7, 26, 18, 30, tzinfo=UTC)

# A scripted page: a fixture name, or an HTTP status code to return instead of a feed.
Scripted = str | int


class FakeFeed:
    """Serves scripted responses per page; a page's script is consumed in order, the last item repeats."""

    def __init__(self, pages: dict[int, list[Scripted]]) -> None:
        self.pages = pages
        self.requested: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        page = int(request.url.path.split("page=")[1].split("/")[0])
        self.requested.append(page)
        script = self.pages.get(page, ["page_empty"])
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, int):
            return httpx.Response(item)
        return httpx.Response(200, json=json.loads((FEED_FIXTURES / f"{item}.json").read_text()))


def make_client(  # craftsman-ignore: WARN-PY001 (regex miscounts the return tuple)
    pages: dict[int, list[Scripted]], max_pages: int = 10
) -> tuple[AppStoreFeedClient, FakeFeed, list[float]]:
    feed = FakeFeed(pages)
    sleeps: list[float] = []
    record_sleep: Callable[[float], None] = sleeps.append
    client = AppStoreFeedClient(
        "1404871703",
        http=httpx.Client(transport=httpx.MockTransport(feed.handler)),
        max_pages=max_pages,
        sleep=record_sleep,
    )
    return client, feed, sleeps


def test_parses_entries_and_skips_metadata() -> None:  # ING-15
    client, _, _ = make_client({1: ["page_full"]}, max_pages=1)
    reviews, _ = client.fetch(SINCE)
    assert [(review.native_id, review.rating) for review in reviews] == [("9001", 5), ("9002", 2)]
    first = reviews[0]
    assert (first.source, first.title, first.text, first.app_version) == (
        "app_store",
        "Easy SIPs",
        "Starting a SIP took two minutes.",
        "18.22.0",
    )


def test_feed_timestamps_converted_to_utc() -> None:  # ING-20
    client, _, _ = make_client({1: ["page_full"]}, max_pages=1)
    reviews, _ = client.fetch(SINCE)
    assert reviews[0].posted_at == datetime(2026, 10, 14, 17, 0, tzinfo=UTC)


def test_single_entry_dict_is_handled() -> None:
    client, _, _ = make_client({1: ["page_single"]}, max_pages=1)
    reviews, _ = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["9005"]


def test_empty_page_is_retried_then_skipped() -> None:  # ING-19
    client, _, sleeps = make_client({1: ["page_empty"], 2: ["page_full"], 3: ["page_old"]})
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["9001", "9002", "9003"]
    assert (report.pages_empty, report.pages_with_entries, report.retries) == (1, 2, 2)
    assert sleeps == [2.0, 4.0]
    assert "app_store_feed_empty_pages:1" in report.warnings


def test_empty_page_that_recovers_on_retry_is_used() -> None:
    client, _, sleeps = make_client({1: ["page_empty", "page_full"], 2: ["page_old"]})
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["9001", "9002", "9003"]
    assert (report.pages_empty, report.retries, sleeps) == (0, 1, [2.0])


def test_http_errors_are_retried_then_page_skipped() -> None:  # ING-17
    client, _, _ = make_client({1: [503], 2: ["page_full"], 3: ["page_old"]})
    reviews, report = client.fetch(SINCE)
    assert len(reviews) == 3 and report.pages_empty == 1


def test_stops_when_older_than_since() -> None:
    client, feed, _ = make_client({1: ["page_full"], 2: ["page_old"], 3: ["page_full"]})
    reviews, report = client.fetch(SINCE)
    assert feed.requested == [1, 2]
    assert "9004" not in {review.native_id for review in reviews}
    assert report.oldest == datetime(2026, 8, 1, 16, 0, tzinfo=UTC)
    assert report.newest == datetime(2026, 10, 14, 17, 0, tzinfo=UTC)
    assert not report.cap_reached


def test_warns_when_feed_cap_reached() -> None:  # ING-16
    client, _, _ = make_client({1: ["page_full"], 2: ["page_single"]}, max_pages=2)
    _, report = client.fetch(SINCE)
    assert report.cap_reached and "app_store_feed_cap_reached" in report.warnings


def test_duplicate_ids_across_pages_are_kept_once() -> None:
    client, _, _ = make_client({1: ["page_full"], 2: ["page_full"], 3: ["page_old"]})
    reviews, _ = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["9001", "9002", "9003"]


def test_author_never_read() -> None:
    client, _, _ = make_client({1: ["page_full"]}, max_pages=1)
    reviews, _ = client.fetch(SINCE)
    assert "REDACTED" not in "".join(review.model_dump_json() for review in reviews)


def test_request_url_targets_storefront_and_app() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=json.loads((FEED_FIXTURES / "page_old.json").read_text()))

    client = AppStoreFeedClient("1404871703", http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.fetch(SINCE)
    assert seen[0] == (
        "https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404871703/sortby=mostrecent/json"
    )


def test_unexpected_json_shapes_are_treated_as_empty_pages() -> None:  # review #2
    bodies = iter([[1, 2], {"feed": None}, "text"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = next(bodies, None)
        if body is None:
            return httpx.Response(200, json=json.loads((FEED_FIXTURES / "page_old.json").read_text()))
        return httpx.Response(200, json=body)

    client = AppStoreFeedClient(
        "1404871703", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda seconds: None
    )
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["9003"] and report.pages_empty == 1


def test_entry_with_null_or_string_fields_is_not_fatal() -> None:  # review #2
    page = json.loads((FEED_FIXTURES / "page_old.json").read_text())
    page["feed"]["entry"][0]["title"] = None
    page["feed"]["entry"][1]["content"] = "plain string"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    client = AppStoreFeedClient("1404871703", http=httpx.Client(transport=httpx.MockTransport(handler)))
    reviews, _ = client.fetch(SINCE)
    assert [(review.native_id, review.title) for review in reviews] == [("9003", None)]
