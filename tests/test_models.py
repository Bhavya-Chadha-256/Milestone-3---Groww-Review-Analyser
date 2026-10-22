from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pulse.models import RawReview

PACIFIC = timezone(timedelta(hours=-7))


def raw_review(**overrides: object) -> RawReview:
    fields: dict[str, object] = {
        "source": "app_store",
        "native_id": "123",
        "rating": 4,
        "title": "Good",
        "text": "Easy to start SIPs",
        "posted_at": datetime(2026, 10, 14, 10, 0, tzinfo=PACIFIC),
        "app_version": "18.22.0",
    }
    fields.update(overrides)
    return RawReview(**fields)


def test_raw_review_converts_posted_at_to_utc() -> None:
    assert raw_review().posted_at == datetime(2026, 10, 14, 17, 0, tzinfo=UTC)
    assert raw_review().posted_at.tzinfo == UTC


def test_raw_review_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        raw_review(posted_at=datetime(2026, 10, 14, 10, 0))


@pytest.mark.parametrize("rating", [0, 6])
def test_raw_review_rejects_out_of_range_rating(rating: int) -> None:
    with pytest.raises(ValidationError):
        raw_review(rating=rating)


def test_raw_review_rejects_empty_native_id() -> None:
    with pytest.raises(ValidationError):
        raw_review(native_id="")


def test_raw_review_has_no_author_field() -> None:
    assert not any("author" in name or "user" in name for name in RawReview.model_fields)
