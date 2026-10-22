from datetime import UTC, datetime
from typing import Any

from pulse.acquire.play_store import PlayStoreClient

SINCE = datetime(2026, 7, 26, 18, 30, tzinfo=UTC)
NEW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
OLD = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

# A scripted page: a list of review dicts, or an exception the fetch raises instead.
Scripted = list[dict[str, Any]] | Exception


def entry(review_id: str, posted: datetime | None = NEW, score: Any = 3, content: str = "Fine app") -> dict:
    """A review dict shaped like google-play-scraper's output, with author fields set to REDACTED."""
    return {
        "reviewId": review_id,
        "userName": "REDACTED",
        "userImage": "REDACTED",
        "content": content,
        "score": score,
        "thumbsUpCount": 0,
        "reviewCreatedVersion": "18.22.0",
        "at": posted,
        "replyContent": "REDACTED",
        "repliedAt": None,
        "appVersion": "18.23.0",
    }


class FakePlay:
    """Serves scripted pages; a page's script is consumed in order, the last item repeats."""

    def __init__(self, pages: list[list[Scripted]]) -> None:
        self.pages = pages
        self.requested: list[int] = []

    def fetch_page(self, token: Any) -> tuple[list[dict[str, Any]], Any]:
        index = 0 if token is None else int(token)
        self.requested.append(index)
        script = self.pages[index]
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, Exception):
            raise item
        next_token = str(index + 1) if index + 1 < len(self.pages) else None
        return item, next_token


def make_client(  # craftsman-ignore: WARN-PY001, PY003 (return hint is on the closing line)
    pages: list[list[Scripted]], max_reviews: int = 20000
) -> tuple[PlayStoreClient, FakePlay, list[float]]:
    play = FakePlay(pages)
    sleeps: list[float] = []
    client = PlayStoreClient(
        "com.nextbillion.groww", max_reviews=max_reviews, fetch_page=play.fetch_page, sleep=sleeps.append
    )
    return client, play, sleeps


def test_maps_fields_and_never_reads_author() -> None:  # PII-3
    client, _, _ = make_client([[[entry("a", content="Withdrawal stuck")]]])
    reviews, report = client.fetch(SINCE)
    assert len(reviews) == 1 and report.reviews == 1
    review = reviews[0]
    assert (review.source, review.native_id, review.rating, review.title) == ("play_store", "a", 3, None)
    assert (review.text, review.posted_at, review.app_version) == ("Withdrawal stuck", NEW, "18.22.0")
    assert "REDACTED" not in review.model_dump_json()


def test_app_version_falls_back_to_current_version() -> None:
    page = entry("a")
    page["reviewCreatedVersion"] = None
    client, _, _ = make_client([[[page]]])
    assert client.fetch(SINCE)[0][0].app_version == "18.23.0"


def test_naive_local_timestamps_are_converted_to_utc() -> None:  # ING-28
    stamp = 1792000000
    client, _, _ = make_client([[[entry("a", posted=datetime.fromtimestamp(stamp))]]])
    assert client.fetch(SINCE)[0][0].posted_at == datetime.fromtimestamp(stamp, UTC)


def test_stops_at_since_and_drops_older_reviews() -> None:
    client, play, _ = make_client(
        [[[entry("a"), entry("b")]], [[entry("c"), entry("d", posted=OLD)]], [[entry("e")]]]
    )
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a", "b", "c"]
    assert play.requested == [0, 1] and report.pages_requested == 2
    assert not report.cap_reached and report.warnings == []


def test_follows_continuation_tokens_with_pause() -> None:
    client, play, sleeps = make_client([[[entry("a")]], [[entry("b")]], [[entry("c")]]])
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a", "b", "c"]
    assert play.requested == [0, 1, 2] and sleeps == [1.0, 1.0]
    assert report.pages_with_entries == 3


def test_duplicate_ids_are_kept_once() -> None:
    client, _, _ = make_client([[[entry("a"), entry("b")]], [[entry("b"), entry("c")]]])
    assert [review.native_id for review in client.fetch(SINCE)[0]] == ["a", "b", "c"]


def test_warns_when_max_reviews_reached() -> None:  # ING-27
    client, play, _ = make_client(
        [[[entry("a"), entry("b")]], [[entry("c"), entry("d")]], [[entry("e")]]], max_reviews=3
    )
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a", "b", "c"]
    assert play.requested == [0, 1]
    assert report.cap_reached and "play_store_cap_reached" in report.warnings


def test_page_errors_are_retried_then_fetch_stops_with_warning() -> None:  # ING-29
    boom = ConnectionError("network down")
    client, play, sleeps = make_client([[[entry("a")]], [boom], [[entry("c")]]])
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a"]
    assert play.requested == [0, 1, 1, 1]
    assert sleeps == [1.0, 2.0, 4.0] and report.retries == 2
    assert "play_store_fetch_failed" in report.warnings


def test_page_error_that_recovers_on_retry_is_used() -> None:
    client, _, _ = make_client([[ConnectionError("blip"), [entry("a")]]])
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a"] and report.retries == 1
    assert report.warnings == []


def test_invalid_entries_are_skipped() -> None:
    missing_score = entry("b")
    del missing_score["score"]
    page = [entry("a"), missing_score, entry("c", score=0), entry("d", posted=None), entry("")]
    client, _, _ = make_client([[page]])
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a"]
    assert report.invalid_entries == 4 and "play_store_invalid_entries:4" in report.warnings


class LibraryToken:
    """Mimics google-play-scraper's _ContinuationToken, which wraps None at the end of the reviews."""

    def __init__(self, token: str | None) -> None:
        self.token = token


def test_empty_page_is_retried_then_fetch_stops_with_warning() -> None:  # ING-29
    client, play, sleeps = make_client([[[entry("a")]], [[]], [[entry("c")]]])
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a"]
    assert play.requested == [0, 1, 1, 1] and report.pages_empty == 1
    assert sleeps == [1.0, 2.0, 4.0] and "play_store_fetch_failed" in report.warnings


def test_swallowed_library_error_is_retried_with_the_previous_token() -> None:  # ING-29
    calls: list[object] = []
    pages = iter(
        [([entry("a")], LibraryToken("next")), ([], LibraryToken(None)), ([entry("b")], LibraryToken(None))]
    )

    def fetch_page(token: object) -> tuple[list[dict], object]:
        calls.append(token)
        return next(pages)

    client = PlayStoreClient("com.nextbillion.groww", fetch_page=fetch_page, sleep=lambda seconds: None)
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a", "b"]
    assert calls[1] is calls[2] and report.retries == 1 and report.warnings == []


def test_wrapped_none_token_ends_paging() -> None:
    calls: list[object] = []

    def fetch_page(token: object) -> tuple[list[dict], object]:
        calls.append(token)
        return [entry("a")], LibraryToken(None)

    client = PlayStoreClient("com.nextbillion.groww", fetch_page=fetch_page, sleep=lambda seconds: None)
    reviews, report = client.fetch(SINCE)
    assert [review.native_id for review in reviews] == ["a"]
    assert len(calls) == 1 and report.warnings == []
