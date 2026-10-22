"""Requests, errors, usage accounting and strict wire schemas for LLM calls (architecture.md §5)."""

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from pulse.config import Pricing, StageConfig

UNSUPPORTED_KEYWORDS = frozenset(
    {"maxItems", "minItems", "maxLength", "minLength", "pattern", "format", "title", "default"}
)


@dataclass(frozen=True)
class LLMRequest:
    stage: str
    system: str
    user: str
    schema: type[BaseModel]
    model: str | None = None  # overrides llm.model, e.g. for judges


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    def __init__(self, category: str) -> None:
        super().__init__(f"model refused ({category})")
        self.category = category


class LLMTruncated(LLMError):
    pass


class LLMSchemaError(LLMError):
    pass


class DailyBudgetExhausted(LLMError):
    def __init__(self, used: int, limit: int) -> None:
        super().__init__(f"daily token budget exhausted: {used} of {limit} used")
        self.used = used
        self.limit = limit


@dataclass
class UsageLedger:
    """Token counts per stage and served-model counts; never contains prompt or response text."""

    stages: dict[str, dict[str, int]] = field(default_factory=dict)
    models: Counter[str] = field(default_factory=Counter)

    def add(self, stage: str, usage: Any, *, model: str) -> None:
        totals = self.stages.setdefault(stage, {"input": 0, "output": 0, "requests": 0})
        totals["input"] += usage.prompt_tokens
        totals["output"] += usage.completion_tokens
        totals["requests"] += 1
        self.models[model] += 1

    def by_stage(self) -> dict[str, dict[str, int]]:
        return {stage: dict(totals) for stage, totals in self.stages.items()}

    def served_models(self) -> dict[str, int]:
        return dict(self.models)

    def unexpected_models(self, requested: set[str]) -> int:
        """Calls served by a model that wasn't requested (LLM-11)."""
        return sum(count for model, count in self.models.items() if model not in requested)

    def total_tokens(self) -> int:
        return sum(totals["input"] + totals["output"] for totals in self.stages.values())

    def cost(self, pricing: Pricing) -> float:
        tokens_in = sum(totals["input"] for totals in self.stages.values())
        tokens_out = sum(totals["output"] for totals in self.stages.values())
        return (tokens_in * pricing.input + tokens_out * pricing.output) / 1e6


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def request_tokens(req: LLMRequest, stage: StageConfig) -> int:
    """What a request counts against the per-minute limit: prompt estimate plus output allowance."""
    schema_text = json.dumps(wire_schema(req.schema))
    return (
        estimate_tokens(req.system)
        + estimate_tokens(req.user)
        + estimate_tokens(schema_text)
        + stage.max_tokens
    )


def wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema in strict-mode form: every property required, no extra properties."""
    return _strict(model.model_json_schema())


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {key: _strict(value) for key, value in node.items() if key not in UNSUPPORTED_KEYWORDS}
    if "properties" in node:  # "properties" maps names to schemas, so its keys are kept as they are
        cleaned["properties"] = {name: _strict(value) for name, value in node["properties"].items()}
        cleaned["required"] = list(node["properties"])
        cleaned["additionalProperties"] = False
    return cleaned
