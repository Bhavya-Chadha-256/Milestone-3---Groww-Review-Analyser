"""Incremental review classification within the daily token budget (architecture.md §3.4, Task 3.3).

Results are cached per (review, taxonomy version, prompt version) and saved after every chunk, so an
interrupted or budget-stopped run resumes where it left off (OPS-6, LLM-13).
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, create_model

from pulse.llm.prompts import load_prompt, reviews_json
from pulse.llm.schema import (
    DailyBudgetExhausted,
    LLMRefusal,
    LLMRequest,
    LLMSchemaError,
    LLMTruncated,
    RequestTooLarge,
    estimate_tokens,
)
from pulse.models import UNTHEMED, Classification, Review, Taxonomy
from pulse.store import Store

REVIEW_INPUT_TOKENS = 50  # ~37 tokens of text plus JSON framing (finding D7)
REVIEW_OUTPUT_TOKENS = 30  # one {k, theme_id, sentiment} item plus a share of reasoning


@dataclass
class ClassifyResult:
    classifications: dict[str, Classification] = field(default_factory=dict)
    unclassified: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(
        default_factory=dict
    )  # review_id → refused | truncated | schema | missing
    from_cache: int = 0
    sent: int = 0
    stopped_for_budget: bool = False
    pending_after: int = 0


def prompt_version() -> str:
    """The `prompt_version` header of classify.md, so editing the prompt invalidates the cache (OPS-6)."""
    return load_prompt("classify").version


def make_classify_schema(theme_ids: Sequence[str]) -> type[BaseModel]:
    theme_literal = Literal.__getitem__((*theme_ids, UNTHEMED))
    sentiment = Literal["negative", "mixed", "positive"]
    item = create_model(
        "ClassifyItem", k=(int, ...), theme_id=(theme_literal, ...), sentiment=(sentiment, ...)
    )
    return create_model("ClassifyOutput", items=(list[item], ...))


def estimate_classify_tokens(pending: int, chunk_size: int, system_tokens: int, max_tokens: int) -> int:
    """Expected daily tokens: prompt per chunk, plus review text in and labels out (capped by max_tokens)."""
    chunks = math.ceil(pending / chunk_size)
    output = min(pending * REVIEW_OUTPUT_TOKENS, chunks * max_tokens)
    return chunks * system_tokens + pending * REVIEW_INPUT_TOKENS + output


def classify_reviews(  # craftsman-ignore: WARN-PY001, PY003 (plan interface; hint on closing line)
    llm: object, store: Store, reviews: Sequence[Review], taxonomy: Taxonomy, *, chunk_size: int = 50
) -> ClassifyResult:
    cached = store.get_classifications(
        [review.review_id for review in reviews], taxonomy.version, prompt_version()
    )
    pending = [review for review in reviews if review.review_id not in cached]
    result = ClassifyResult(classifications=dict(cached), from_cache=len(cached))
    classifier = _ChunkClassifier(llm, taxonomy)
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start : start + chunk_size]
        try:
            labels, missing = classifier.classify(chunk)
        except DailyBudgetExhausted:
            result.stopped_for_budget = True
            result.pending_after = len(pending) - start
            break
        store.save_classifications(labels.values())
        result.classifications.update(labels)
        result.unclassified += list(missing)
        result.failures.update(missing)
        result.sent += len(chunk)
    return result


@dataclass
class _Outcome:
    labels: dict[str, Classification] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)  # keys the model skipped: re-queued once (LLM-4)
    failed: dict[str, str] = field(default_factory=dict)  # refused, truncated or invalid: not retried again

    def __or__(self, other: "_Outcome") -> "_Outcome":
        return _Outcome(self.labels | other.labels, self.missing + other.missing, self.failed | other.failed)


class _ChunkClassifier:
    """Classifies one chunk: splits on truncation or refusal, re-queues skipped keys once."""

    def __init__(self, llm: object, taxonomy: Taxonomy) -> None:
        self.llm = llm
        self.taxonomy = taxonomy
        self.schema = make_classify_schema([theme.id for theme in taxonomy.themes])
        self.system = _system_prompt(taxonomy)
        self.prompt_version = prompt_version()

    def classify(self, chunk: Sequence[Review]) -> tuple[dict[str, Classification], dict[str, str]]:
        """Labels by review ID, and the reviews left unlabelled with their failure class."""
        outcome = self._ask(chunk, refusal_split=True)
        if outcome.missing:
            skipped = set(outcome.missing)
            retried = self._ask(
                [review for review in chunk if review.review_id in skipped], refusal_split=True
            )
            outcome = _Outcome(
                outcome.labels | retried.labels, retried.missing, outcome.failed | retried.failed
            )
        return outcome.labels, outcome.failed | dict.fromkeys(outcome.missing, "missing")

    def _ask(self, chunk: Sequence[Review], refusal_split: bool) -> _Outcome:
        payload, keys = reviews_json(chunk)
        user = f"Classify every review below. Return one item per key `k`.\n{payload}"
        try:
            output = self.llm.structured(LLMRequest("classify", self.system, user, self.schema))
        except (LLMTruncated, RequestTooLarge):  # LLM-2, LLM-12: halve until a single review still fails
            return self._split(chunk, refusal_split) if len(chunk) > 1 else _failed(chunk, "truncated")
        except LLMRefusal:  # LLM-1: one split retry, then give up on those reviews
            can_split = refusal_split and len(chunk) > 1
            return self._split(chunk, refusal_split=False) if can_split else _failed(chunk, "refused")
        except LLMSchemaError:
            return _failed(chunk, "schema")
        labels = {
            keys[item.k]: Classification(
                review_id=keys[item.k],
                theme_id=item.theme_id,
                sentiment=item.sentiment,
                taxonomy_version=self.taxonomy.version,
                prompt_version=self.prompt_version,
            )
            for item in output.items
            if item.k in keys  # unknown keys are dropped
        }
        return _Outcome(labels, [review_id for review_id in _ids(chunk) if review_id not in labels])

    def _split(self, chunk: Sequence[Review], refusal_split: bool) -> _Outcome:
        middle = len(chunk) // 2
        return self._ask(chunk[:middle], refusal_split) | self._ask(chunk[middle:], refusal_split)


def _failed(chunk: Sequence[Review], failure_class: str) -> _Outcome:
    return _Outcome(failed=dict.fromkeys(_ids(chunk), failure_class))


def _ids(chunk: Sequence[Review]) -> list[str]:
    return [review.review_id for review in chunk]


def _system_prompt(taxonomy: Taxonomy) -> str:
    themes = [
        {
            "id": theme.id,
            "name": theme.name,
            "definition": theme.definition,
            "include_hints": theme.include_hints,
            "exclude_hints": theme.exclude_hints,
        }
        for theme in taxonomy.themes
    ]
    catalogue = json.dumps(themes, ensure_ascii=False)
    return f"{load_prompt('classify').text}\nThemes (or `{UNTHEMED}`):\n{catalogue}\n"


def system_tokens(taxonomy: Taxonomy) -> int:
    return estimate_tokens(_system_prompt(taxonomy))
