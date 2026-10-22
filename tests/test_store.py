from datetime import UTC, date, datetime
from pathlib import Path

from factories import make_classification, make_review

from pulse.models import Delivery, RunStatus, Taxonomy, Theme
from pulse.store import Store, UpsertResult
from pulse.timeutil import window_for_week

EARLY = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
LATE = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)


def make_taxonomy(version: int) -> Taxonomy:
    theme = Theme(
        id="withdrawals", name="Withdrawals", definition="Money out", include_hints=[], exclude_hints=[]
    )
    return Taxonomy(
        version=version,
        themes=[theme],
        created_from_run="2026-W42-x",
        created_at=LATE,
        sample_review_ids=["a"],
    )


def test_store_creates_missing_parent_directory(tmp_path: Path) -> None:
    Store(tmp_path / "fresh" / "data" / "pulse.db")
    assert (tmp_path / "fresh" / "data" / "pulse.db").is_file()


def test_upsert_reviews_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    reviews = [make_review(review_id="a"), make_review(review_id="b")]
    assert store.upsert_reviews(reviews) == UpsertResult(new=2, updated=0)
    assert store.upsert_reviews(reviews) == UpsertResult(new=0, updated=0)


def test_upsert_replaces_edited_review_and_invalidates_classification(tmp_path: Path) -> None:  # ING-5
    store = Store(tmp_path / "pulse.db")
    store.upsert_reviews([make_review(review_id="a", text="slow app", text_hash="h1")])
    store.save_classifications([make_classification("a", "performance")])
    result = store.upsert_reviews([make_review(review_id="a", text="withdrawal stuck", text_hash="h2")])
    assert result == UpsertResult(new=0, updated=1)
    assert store.get_classifications(["a"], 1, "classify-v1") == {}
    assert store.reviews_between(EARLY, datetime(2027, 1, 1, tzinfo=UTC))[0].text == "withdrawal stuck"


def test_rating_edit_keeps_classification(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    store.upsert_reviews([make_review(review_id="a", rating=1)])
    store.save_classifications([make_classification("a", "performance")])
    assert store.upsert_reviews([make_review(review_id="a", rating=4)]) == UpsertResult(new=0, updated=1)
    assert set(store.get_classifications(["a"], 1, "classify-v1")) == {"a"}


def test_reviews_between_is_half_open_and_ordered(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    store.upsert_reviews(
        [
            make_review(review_id="late", posted_at=LATE),
            make_review(review_id="early", posted_at=EARLY),
            make_review(review_id="edge", posted_at=datetime(2026, 10, 18, 18, 30, tzinfo=UTC)),
        ]
    )
    found = store.reviews_between(EARLY, datetime(2026, 10, 18, 18, 30, tzinfo=UTC))
    assert [review.review_id for review in found] == ["early", "late"]
    assert found[0].posted_at == EARLY


def test_reviews_between_can_skip_excluded(tmp_path: Path) -> None:  # ING-26
    store = Store(tmp_path / "pulse.db")
    store.upsert_reviews(
        [make_review(review_id="long"), make_review(review_id="short", excluded_reason="too_short")]
    )
    everything = store.reviews_between(EARLY, datetime(2027, 1, 1, tzinfo=UTC))
    eligible = store.reviews_between(EARLY, datetime(2027, 1, 1, tzinfo=UTC), eligible_only=True)
    assert sorted(review.review_id for review in everything) == ["long", "short"]
    assert [review.review_id for review in eligible] == ["long"]


def test_classification_cache_is_keyed_by_versions(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    store.save_classifications(
        [
            make_classification("a", "performance"),
            make_classification("a", "withdrawals", taxonomy_version=2),
            make_classification("b", "kyc", prompt_version="classify-v2"),
        ]
    )
    assert {
        key: value.theme_id for key, value in store.get_classifications(["a", "b"], 1, "classify-v1").items()
    } == {"a": "performance"}
    assert store.get_classifications(["a"], 2, "classify-v1")["a"].theme_id == "withdrawals"
    assert set(store.get_classifications(["b"], 1, "classify-v2")) == {"b"}


def test_classification_lookup_handles_many_ids(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    ids = [f"id{index}" for index in range(2500)]
    store.save_classifications([make_classification(review_id, "kyc") for review_id in ids])
    assert len(store.get_classifications(ids, 1, "classify-v1")) == 2500


def test_taxonomy_versions(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    assert store.latest_taxonomy() is None and store.next_taxonomy_version() == 1
    store.save_taxonomy(make_taxonomy(1))
    store.save_taxonomy(make_taxonomy(2))
    assert store.latest_taxonomy() == make_taxonomy(2)
    assert store.next_taxonomy_version() == 3


def test_run_status_merges_summary(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    assert store.latest_run("2026-W42") is None
    window = window_for_week("2026-W42", 12, "Asia/Kolkata")
    store.create_run("2026-W42-20261021T050000", window)
    store.set_run_status("2026-W42-20261021T050000", RunStatus.INGESTED, reviews=10)
    store.set_run_status("2026-W42-20261021T050000", RunStatus.INGESTED, skipped=2)
    run = store.latest_run("2026-W42")
    assert run is not None
    assert (run["run_id"], run["status"]) == ("2026-W42-20261021T050000", "INGESTED")
    assert run["summary"] == {"reviews": 10, "skipped": 2}
    assert run["window"]["start"] == date(2026, 7, 27).isoformat()


def test_latest_run_is_most_recent(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    window = window_for_week("2026-W42", 12, "Asia/Kolkata")
    store.create_run("2026-W42-20261021T050000", window)
    store.create_run("2026-W42-20261021T060000", window)
    run = store.latest_run("2026-W42")
    assert run is not None and run["run_id"] == "2026-W42-20261021T060000"


def test_delivery_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "pulse.db")
    assert store.get_delivery("2026-W42") is None
    delivery = Delivery(iso_week="2026-W42", doc_id="doc1", doc_url="https://docs.example/doc1")
    store.save_delivery(delivery)
    store.save_delivery(delivery.model_copy(update={"doc_verified": True, "draft_id": "d1"}))
    saved = store.get_delivery("2026-W42")
    assert saved is not None and saved.doc_verified and saved.draft_id == "d1"
