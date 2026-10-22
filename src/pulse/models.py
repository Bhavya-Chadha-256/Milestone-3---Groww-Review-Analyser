"""Shared data models: RawReview (Task 1.1) and the normalised models (Task 2.1, architecture.md §6)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from annotated_types import Ge as AtLeast
from annotated_types import Le as AtMost
from annotated_types import MinLen
from pydantic import AwareDatetime, BaseModel, ConfigDict, PositiveInt, field_validator

Source = Literal["play_store", "app_store"]


class RawReview(BaseModel):
    """One review as read from a public source. Deliberately has no author/user/device fields."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    native_id: Annotated[str, MinLen(1)]
    rating: Annotated[int, AtLeast(1), AtMost(5)]
    title: str | None
    text: str
    posted_at: AwareDatetime
    app_version: str | None

    @field_validator("posted_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


Language = Literal["en", "hi", "hinglish", "other"]
Exclusion = Literal["too_short", "unsupported_language"]
UNTHEMED = "unthemed"


class Review(BaseModel):
    """A PII-free, normalised review: the only form later stages ever see (architecture.md §6)."""

    model_config = ConfigDict(extra="forbid")

    review_id: Annotated[str, MinLen(1)]  # sha256(source + ":" + native_id)[:16]
    source: Source
    rating: Annotated[int, AtLeast(1), AtMost(5)]
    title: str | None  # scrubbed
    text: str  # scrubbed
    posted_at: AwareDatetime  # UTC; weeks are derived in IST
    app_version: str | None
    language: Language
    word_count: Annotated[int, AtLeast(0)]
    text_hash: str  # detects edited reviews
    is_duplicate_text: bool = False
    excluded_reason: Exclusion | None = None  # stored and counted, but never themed or quoted (§3.2)

    @field_validator("posted_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class Theme(BaseModel):
    id: str  # slug, e.g. "withdrawals"
    name: str  # ≤ 4 words
    definition: str
    include_hints: list[str]
    exclude_hints: list[str]


class Taxonomy(BaseModel):
    version: PositiveInt
    themes: list[Theme]  # 3 ≤ len ≤ 5 (checked at discovery)
    created_from_run: str
    created_at: AwareDatetime
    sample_review_ids: list[str]  # excluded from the gold eval set


class Classification(BaseModel):
    review_id: str
    theme_id: str  # a Theme.id or UNTHEMED
    sentiment: Literal["negative", "mixed", "positive"]
    taxonomy_version: PositiveInt
    prompt_version: str


class Quote(BaseModel):
    review_id: str
    theme_id: str
    text: str  # verified substring of Review.text
    rating: Annotated[int, AtLeast(1), AtMost(5)]
    source: str
    month: str  # "Oct 2026", in IST


class Delivery(BaseModel):
    iso_week: str
    doc_id: str | None = None
    doc_url: str | None = None
    doc_verified: bool = False
    draft_id: str | None = None
    delivered_at: AwareDatetime | None = None


class RunStatus(StrEnum):
    INGESTED = "INGESTED"
    CLASSIFIED = "CLASSIFIED"
    AGGREGATED = "AGGREGATED"
    QUOTED = "QUOTED"
    COMPOSED = "COMPOSED"
    VALIDATED = "VALIDATED"
    PUBLISHED_DOC_ONLY = "PUBLISHED_DOC_ONLY"
    DELIVERY_UNVERIFIED = "DELIVERY_UNVERIFIED"
    DELIVERED = "DELIVERED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"
