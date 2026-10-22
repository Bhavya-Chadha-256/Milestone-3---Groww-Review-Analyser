import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from factories import make_classification, make_review
from fakes import FakeLLM
from pydantic import BaseModel

from pulse.llm.classify import (
    classify_reviews,
    estimate_classify_tokens,
    make_classify_schema,
    prompt_version,
)
from pulse.llm.prompts import DATA_RULE, Prompt, load_prompt
from pulse.llm.schema import LLMRefusal, LLMRequest, LLMTruncated, RequestTooLarge
from pulse.models import Review, Taxonomy, Theme
from pulse.store import Store

CHUNK_TOKENS = 1000


def taxonomy() -> Taxonomy:
    themes = [
        Theme(id=slug, name=slug.title(), definition=f"About {slug}", include_hints=[slug], exclude_hints=[])
        for slug in ("support", "charges", "bugs")
    ]
    return Taxonomy(
        version=1,
        themes=themes,
        created_from_run="r",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        sample_review_ids=[],
    )


def reviews(count: int) -> list[Review]:
    return [make_review(review_id=f"r{index:04d}") for index in range(count)]


def keys_in(req: LLMRequest) -> list[int]:
    payload = req.user[req.user.index("<reviews>") + len("<reviews>") : req.user.index("</reviews>")]
    return [item["k"] for item in json.loads(payload)]


def echo_handler(
    theme: str = "support", skip: set[int] | None = None, extra: list[int] | None = None
) -> Callable:
    def handle(req: LLMRequest) -> BaseModel:
        keys = [key for key in keys_in(req) if key not in (skip or set())] + (extra or [])
        items = [{"k": key, "theme_id": theme, "sentiment": "negative"} for key in keys]
        return req.schema.model_validate({"items": items})

    return handle


def sizes(llm: FakeLLM) -> list[int]:
    return [len(keys_in(call)) for call in llm.calls]


def test_schema_enum_is_taxonomy_ids_plus_unthemed() -> None:
    schema = make_classify_schema(["support", "charges"]).model_json_schema()
    item = schema["$defs"][next(iter(schema["$defs"]))]
    assert item["properties"]["theme_id"]["enum"] == ["support", "charges", "unthemed"]
    assert item["properties"]["sentiment"]["enum"] == ["negative", "mixed", "positive"]


def test_skips_cached_reviews(tmp_store: Store) -> None:
    batch = reviews(10)
    tmp_store.save_classifications([make_classification(review.review_id, "bugs") for review in batch[:4]])
    llm = FakeLLM({"classify": echo_handler()})
    result = classify_reviews(llm, tmp_store, batch, taxonomy())
    assert sizes(llm) == [6] and (result.from_cache, result.sent) == (4, 6)
    assert len(result.classifications) == 10


def test_chunks_by_fifty(tmp_store: Store) -> None:
    llm = FakeLLM({"classify": echo_handler()})
    classify_reviews(llm, tmp_store, reviews(120), taxonomy())
    assert sizes(llm) == [50, 50, 20]


def test_short_keys_map_back_to_review_ids(tmp_store: Store) -> None:
    llm = FakeLLM({"classify": echo_handler("charges")})
    result = classify_reviews(llm, tmp_store, reviews(3), taxonomy())
    assert sorted(result.classifications) == ["r0000", "r0001", "r0002"]
    saved = tmp_store.get_classifications(["r0001"], 1, prompt_version())["r0001"]
    assert (saved.theme_id, saved.sentiment, saved.prompt_version) == (
        "charges",
        "negative",
        prompt_version(),
    )
    assert "r0001" not in llm.calls[0].user


def test_unknown_ids_dropped_and_missing_requeued_once(tmp_store: Store) -> None:  # LLM-4
    first, second = echo_handler(skip={2}, extra=[999]), echo_handler()
    llm = FakeLLM({"classify": lambda req: (first if len(llm.calls) == 1 else second)(req)})
    result = classify_reviews(llm, tmp_store, reviews(5), taxonomy())
    assert sizes(llm) == [5, 1] and len(result.classifications) == 5 and result.unclassified == []


def test_missing_twice_is_unclassified(tmp_store: Store) -> None:
    llm = FakeLLM({"classify": echo_handler(skip={1})})
    result = classify_reviews(llm, tmp_store, reviews(5), taxonomy())
    assert result.unclassified == ["r0000"] and len(result.classifications) == 4


def test_truncation_splits_chunk(tmp_store: Store) -> None:  # LLM-2
    def handle(req: LLMRequest) -> BaseModel:
        if len(keys_in(req)) >= 50:
            raise LLMTruncated("too long")
        return echo_handler()(req)

    llm = FakeLLM({"classify": handle})
    result = classify_reviews(llm, tmp_store, reviews(50), taxonomy())
    assert sizes(llm) == [50, 25, 25] and len(result.classifications) == 50


def test_single_review_that_truncates_is_unclassified(tmp_store: Store) -> None:
    llm = FakeLLM({"classify": [LLMTruncated("too long")]})
    result = classify_reviews(llm, tmp_store, reviews(1), taxonomy())
    assert result.unclassified == ["r0000"]


def test_refused_chunk_is_retried_once(tmp_store: Store, tmp_path: Path) -> None:  # LLM-1
    def handle(req: LLMRequest) -> BaseModel:
        if len(keys_in(req)) == 50:
            raise LLMRefusal("content_filter")
        return echo_handler()(req)

    llm = FakeLLM({"classify": handle})
    assert len(classify_reviews(llm, tmp_store, reviews(50), taxonomy()).classifications) == 50
    assert sizes(llm) == [50, 25, 25]
    always = FakeLLM({"classify": [LLMRefusal("content_filter")]})
    result = classify_reviews(always, Store(tmp_path / "second.db"), reviews(50), taxonomy())
    assert len(result.unclassified) == 50 and sizes(always) == [50, 25, 25]


def test_interrupted_classification_resumes_from_cache(tmp_store: Store) -> None:  # OPS-6
    def handle(req: LLMRequest) -> BaseModel:
        if len(llm.calls) == 3:
            raise ConnectionError("network down")
        return echo_handler()(req)

    llm = FakeLLM({"classify": handle})
    with pytest.raises(ConnectionError):
        classify_reviews(llm, tmp_store, reviews(150), taxonomy())
    again = FakeLLM({"classify": echo_handler()})
    result = classify_reviews(again, tmp_store, reviews(150), taxonomy())
    assert sizes(again) == [50] and result.from_cache == 100


def test_classification_stops_at_daily_budget_and_resumes(tmp_store: Store) -> None:  # LLM-13
    llm = FakeLLM({"classify": echo_handler()}, tokens_left=2 * CHUNK_TOKENS, tokens_per_call=CHUNK_TOKENS)
    first = classify_reviews(llm, tmp_store, reviews(150), taxonomy())
    assert first.stopped_for_budget and first.pending_after == 50
    second = classify_reviews(FakeLLM({"classify": echo_handler()}), tmp_store, reviews(150), taxonomy())
    assert second.sent == 50 and not second.stopped_for_budget and second.pending_after == 0


def test_estimate_classify_tokens_scales_with_chunks() -> None:
    small = estimate_classify_tokens(50, 50, system_tokens=600, max_tokens=2500)
    large = estimate_classify_tokens(2177, 50, system_tokens=600, max_tokens=2500)
    assert 0 < small < large and 150_000 < large < 300_000


def test_classify_prompt_contains_data_rule_and_taxonomy(tmp_store: Store) -> None:
    llm = FakeLLM({"classify": echo_handler()})
    classify_reviews(llm, tmp_store, reviews(2), taxonomy())
    system = llm.calls[0].system
    assert DATA_RULE in system and all(slug in system for slug in ("support", "charges", "bugs", "unthemed"))
    assert "names no product area" in system and "Hinglish" in system and "mixed" in system


def test_chunk_larger_than_tpm_is_split(tmp_store: Store) -> None:  # LLM-12, review I2
    def handle(req: LLMRequest) -> BaseModel:
        if len(keys_in(req)) > 20:
            raise RequestTooLarge("a 9000-token request can never fit limits.tpm (8000)")
        return echo_handler()(req)

    llm = FakeLLM({"classify": handle})
    result = classify_reviews(llm, tmp_store, reviews(50), taxonomy())
    assert len(result.classifications) == 50 and sizes(llm) == [50, 25, 12, 13, 25, 12, 13]


def test_prompt_version_comes_from_prompt_file(
    tmp_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:  # review I4
    tmp_store.save_classifications([make_classification(f"r{index:04d}", "bugs") for index in range(5)])
    bumped = Prompt(version="classify-v2", text=load_prompt("classify").text)
    monkeypatch.setattr("pulse.llm.classify.load_prompt", lambda name: bumped)
    llm = FakeLLM({"classify": echo_handler()})
    result = classify_reviews(llm, tmp_store, reviews(5), taxonomy())
    assert result.from_cache == 0 and result.sent == 5
    assert {label.prompt_version for label in result.classifications.values()} == {"classify-v2"}
