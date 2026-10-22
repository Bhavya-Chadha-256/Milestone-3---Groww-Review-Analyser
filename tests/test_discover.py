from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from factories import make_review
from fakes import FakeLLM

from pulse.analysis.sample import stratified_sample
from pulse.config import Settings
from pulse.llm.discover import (
    DiscoveredTheme,
    DiscoveryOutput,
    TaxonomyError,
    discover_taxonomy,
    needs_rediscovery,
)
from pulse.llm.prompts import DATA_RULE, load_prompt
from pulse.llm.schema import request_tokens
from pulse.models import Review

TZ = "Asia/Kolkata"
BASE = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def reviews(count: int, **fields: object) -> list[Review]:
    prefix = str(fields.pop("prefix", "r"))
    return [make_review(review_id=f"{prefix}{index:05d}", **fields) for index in range(count)]


def theme(slug: str, name: str | None = None) -> DiscoveredTheme:
    return DiscoveredTheme(
        id=slug,
        name=name or slug.replace("_", " ").title(),
        definition=f"Reviews about {slug}",
        include_hints=[slug],
        exclude_hints=[],
        example_keys=[1, 2, 999],
    )


def themes(count: int) -> list[DiscoveredTheme]:
    return [theme(slug) for slug in ("support", "charges", "app_bugs", "charts", "kyc", "payments")[:count]]


def test_stratified_sample_balances_ratings() -> None:
    pool = reviews(900, rating=5, prefix="five") + reviews(100, rating=1, prefix="one")
    sample = stratified_sample(pool, 200, seed=7, timezone=TZ)
    assert Counter(review.rating for review in sample) == {1: 100, 5: 100}


def test_stratified_sample_excludes_duplicates_and_is_deterministic() -> None:
    pool = reviews(50, prefix="ok") + reviews(10, prefix="dup", is_duplicate_text=True)
    pool += reviews(10, prefix="short", excluded_reason="too_short")
    first = stratified_sample(pool, 40, seed=7, timezone=TZ)
    assert all(review.review_id.startswith("ok") for review in first) and len(first) == 40
    assert [review.review_id for review in first] == [
        review.review_id for review in stratified_sample(pool, 40, 7, TZ)
    ]
    assert first != stratified_sample(pool, 40, seed=8, timezone=TZ)


def test_stratified_sample_balances_weeks() -> None:
    early = reviews(300, prefix="early", posted_at=BASE)
    late = reviews(30, prefix="late", posted_at=BASE + timedelta(days=14))
    sample = stratified_sample(early + late, 60, seed=7, timezone=TZ)
    assert sum(review.review_id.startswith("late") for review in sample) == 30


def test_stratified_sample_keeps_source_proportional() -> None:  # LLM-15
    pool = reviews(950, prefix="play", source="play_store") + reviews(50, prefix="app", source="app_store")
    sample = stratified_sample(pool, 200, seed=7, timezone=TZ)
    assert sum(review.source == "app_store" for review in sample) <= 20


def test_discover_prompt_has_praise_rule_and_hinglish_note() -> None:
    for name in ("discover", "discover_merge"):
        text = load_prompt(name).text
        assert DATA_RULE in text and "Hinglish" in text and "unthemed" in text
        assert "names no product area" in text and "40%" in text


def test_discovery_runs_in_passes_then_merges(settings: Settings) -> None:  # LLM-12
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=themes(3))]})
    taxonomy = discover_taxonomy(llm, reviews(400), [], 1, "run-1", settings=settings.llm)
    assert len(llm.calls) == 5 and len(taxonomy.themes) == 3
    assert "candidate themes" in llm.calls[-1].user.lower()
    stage = settings.llm.stages["discover"]
    assert all(request_tokens(call, stage) < settings.llm.limits.tpm for call in llm.calls)


def test_long_reviews_shrink_the_batches(settings: Settings) -> None:  # LLM-12
    long_text = "withdrawal stuck again " * 60
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=themes(3))]})
    discover_taxonomy(llm, reviews(100, text=long_text), [], 1, "run-1", settings=settings.llm)
    stage = settings.llm.stages["discover"]
    assert len(llm.calls) > 2
    assert all(request_tokens(call, stage) < settings.llm.limits.tpm for call in llm.calls)


def test_discover_returns_versioned_taxonomy_with_sample_ids(settings: Settings) -> None:
    sample = reviews(30)
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=themes(4))]})
    taxonomy = discover_taxonomy(llm, sample, ["customer support"], 3, "2026-W42-x", settings=settings.llm)
    assert (taxonomy.version, taxonomy.created_from_run) == (3, "2026-W42-x")
    assert taxonomy.sample_review_ids == [review.review_id for review in sample]
    assert [item.id for item in taxonomy.themes] == ["support", "charges", "app_bugs", "charts"]
    assert len(llm.calls) == 1 and DATA_RULE in llm.calls[0].system
    assert "customer support" in llm.calls[0].user


def test_discover_reasks_when_more_than_five_themes(settings: Settings) -> None:
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=themes(6)), DiscoveryOutput(themes=themes(5))]})
    assert len(discover_taxonomy(llm, reviews(30), [], 1, "r", settings=settings.llm).themes) == 5
    assert "at most 5" in llm.calls[1].user


def test_discover_reasks_when_fewer_than_three_themes(settings: Settings) -> None:  # LLM-6
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=themes(2)), DiscoveryOutput(themes=themes(3))]})
    assert len(discover_taxonomy(llm, reviews(30), [], 1, "r", settings=settings.llm).themes) == 3
    assert "at least 3" in llm.calls[1].user


def test_discover_reasks_when_theme_name_too_long(settings: Settings) -> None:  # REN-1
    too_long = [theme("withdrawals", "Problems with withdrawing money to bank"), *themes(2)]
    llm = FakeLLM({"discover": [DiscoveryOutput(themes=too_long), DiscoveryOutput(themes=themes(3))]})
    assert len(discover_taxonomy(llm, reviews(30), [], 1, "r", settings=settings.llm).themes) == 3
    assert "withdrawals" in llm.calls[1].user and "4 words" in llm.calls[1].user


def test_discover_rejects_reserved_unthemed_id(settings: Settings) -> None:
    bad = DiscoveryOutput(themes=[theme("unthemed"), *themes(2)])
    llm = FakeLLM({"discover": [bad, bad]})
    with pytest.raises(TaxonomyError):
        discover_taxonomy(llm, reviews(30), [], 1, "r", settings=settings.llm)


def test_discover_rejects_duplicate_and_malformed_ids(settings: Settings) -> None:
    bad = DiscoveryOutput(themes=[theme("support"), theme("support"), theme("Bad-ID")])
    llm = FakeLLM({"discover": [bad, bad]})
    with pytest.raises(TaxonomyError, match="duplicate|id"):
        discover_taxonomy(llm, reviews(30), [], 1, "r", settings=settings.llm)


@pytest.mark.parametrize("case", [(0.21, True), (0.20, False)])
def test_needs_rediscovery(case: tuple[float, bool]) -> None:
    share, expected = case
    assert needs_rediscovery(share, 0.20) is expected
