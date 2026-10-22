import json
from types import SimpleNamespace
from typing import Annotated, Any, Literal

import groq
import httpx
import pytest
from annotated_types import MaxLen, MinLen
from factories import make_review
from pydantic import BaseModel

from pulse.config import Pricing, Settings
from pulse.llm.client import GroqLLM, build_params
from pulse.llm.prompts import DATA_RULE, load_prompt, reviews_json
from pulse.llm.ratelimit import utc_day
from pulse.llm.schema import (
    DailyBudgetExhausted,
    LLMError,
    LLMRefusal,
    LLMRequest,
    LLMSchemaError,
    LLMTruncated,
    UsageLedger,
    wire_schema,
)
from pulse.store import Store

PIPELINE_MODEL = "openai/gpt-oss-120b"
JUDGE_MODEL = "qwen/qwen3.8-27b"


class Out(BaseModel):
    label: Literal["praise", "complaint"]
    note: str | None = None


class Nested(BaseModel):
    tags: Annotated[list[Annotated[str, MaxLen(10)]], MinLen(1)]
    child: Out


def resp(
    content: str = '{"label": "praise", "note": null}', finish: str = "stop", model: str = PIPELINE_MODEL
) -> Any:
    message = SimpleNamespace(content=content)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage, model=model
    )


def api_error(
    cls: type[groq.APIStatusError], status: int, body: dict, headers: dict | None = None
) -> Exception:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return cls(
        json.dumps(body), response=httpx.Response(status, headers=headers or {}, request=request), body=body
    )


class FakeCompletions:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeGroq:
    def __init__(self, script: list[Any]) -> None:
        self.completions = FakeCompletions(script)
        self.chat = SimpleNamespace(completions=self.completions)


def make_llm(settings: Settings, store: Store, script: list[Any]) -> tuple[GroqLLM, FakeGroq, list[float]]:
    fake = FakeGroq(script)
    sleeps: list[float] = []
    return GroqLLM(settings.llm, store, client=fake, sleep=sleeps.append), fake, sleeps


REQUEST = LLMRequest("classify", f"Classify. {DATA_RULE}", "The app is great", Out)


def test_build_params_sets_model_effort_and_strict_schema(settings: Settings) -> None:
    params = build_params(REQUEST, settings.llm)
    assert params["model"] == PIPELINE_MODEL and params["max_completion_tokens"] == 2500
    assert (params["reasoning_effort"], params["include_reasoning"]) == ("low", False)
    assert [message["role"] for message in params["messages"]] == ["system", "user"]
    response_format = params["response_format"]
    assert response_format["type"] == "json_schema"
    assert (
        response_format["json_schema"]["strict"] is True and response_format["json_schema"]["name"] == "Out"
    )
    assert "thinking" not in params and "output_config" not in params


def test_judge_model_params_omit_reasoning_effort(settings: Settings) -> None:
    params = build_params(LLMRequest("judge", "s", "u", Out, model=JUDGE_MODEL), settings.llm)
    assert params["model"] == JUDGE_MODEL
    assert "reasoning_effort" not in params and "include_reasoning" not in params


def test_parses_message_content(settings: Settings, tmp_store: Store) -> None:
    llm, _, _ = make_llm(settings, tmp_store, [resp()])
    assert llm.structured(REQUEST) == Out(label="praise")


def test_content_filter_raises_refusal(settings: Settings, tmp_store: Store) -> None:  # LLM-1
    llm, _, _ = make_llm(settings, tmp_store, [resp(finish="content_filter")])
    with pytest.raises(LLMRefusal):
        llm.structured(REQUEST)


def test_length_finish_raises_truncated(settings: Settings, tmp_store: Store) -> None:  # LLM-2
    llm, _, _ = make_llm(settings, tmp_store, [resp(finish="length")])
    with pytest.raises(LLMTruncated):
        llm.structured(REQUEST)


def test_schema_invalid_reasks_once_then_raises(settings: Settings, tmp_store: Store) -> None:  # LLM-3
    llm, fake, _ = make_llm(settings, tmp_store, [resp('{"label": "nope"}'), resp()])
    assert llm.structured(REQUEST).label == "praise"
    assert "failed validation" in fake.completions.calls[1]["messages"][-1]["content"]
    bad = api_error(groq.BadRequestError, 400, {"error": {"code": "json_validate_failed", "message": "bad"}})
    llm, _, _ = make_llm(settings, tmp_store, [bad, resp('{"oops": 1}')])
    with pytest.raises(LLMSchemaError):
        llm.structured(REQUEST)


def test_rate_limited_call_waits_then_retries(settings: Settings, tmp_store: Store) -> None:  # LLM-10
    limited = api_error(groq.RateLimitError, 429, {"error": {"message": "slow down"}}, {"retry-after": "2"})
    llm, fake, sleeps = make_llm(settings, tmp_store, [limited, resp()])
    assert llm.structured(REQUEST).label == "praise"
    assert len(fake.completions.calls) == 2 and 2.0 in sleeps


def test_daily_budget_stops_before_sending(settings: Settings, tmp_store: Store) -> None:  # LLM-13
    tmp_store.add_llm_usage(utc_day(), PIPELINE_MODEL, 199_000)
    llm, fake, _ = make_llm(settings, tmp_store, [resp()])
    with pytest.raises(DailyBudgetExhausted):
        llm.structured(REQUEST)
    assert fake.completions.calls == []
    assert llm.remaining_tokens_today() == 1000


def test_usage_is_recorded_per_day_and_model(settings: Settings, tmp_store: Store) -> None:
    llm, _, _ = make_llm(settings, tmp_store, [resp()])
    llm.structured(REQUEST)
    assert tmp_store.llm_usage(utc_day(), PIPELINE_MODEL) == (120, 1)
    assert llm.usage.by_stage() == {"classify": {"input": 100, "output": 20, "requests": 1}}


def test_wire_schema_is_strict() -> None:
    schema = wire_schema(Nested)
    text = json.dumps(schema)
    for keyword in ("maxLength", "minItems", '"title"', '"default"'):
        assert keyword not in text

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)


def test_usage_records_served_model(settings: Settings, tmp_store: Store) -> None:  # LLM-11
    llm, _, _ = make_llm(settings, tmp_store, [resp(model="other-model")])
    llm.structured(REQUEST)
    assert llm.usage.served_models() == {"other-model": 1}
    assert llm.usage.unexpected_models({PIPELINE_MODEL, JUDGE_MODEL}) == 1


def test_usage_ledger_cost_is_zero_on_free_tier(settings: Settings) -> None:
    ledger = UsageLedger()
    ledger.add(
        "classify",
        SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=1_000_000),
        model=PIPELINE_MODEL,
    )
    assert ledger.cost(settings.llm.pricing) == 0.0
    assert ledger.cost(Pricing(input=1.0, output=2.0)) == pytest.approx(3.0)
    assert ledger.total_tokens() == 2_000_000


def test_missing_api_key_raises_clear_error(
    settings: Settings, tmp_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:  # OPS-10
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        GroqLLM(settings.llm, tmp_store)


def test_reviews_json_uses_short_keys_and_maps_back() -> None:
    reviews = [
        make_review(review_id="aaaa1111bbbb2222", title=None),
        make_review(review_id="cccc3333dddd4444", title="Slow"),
    ]
    payload, keys = reviews_json(reviews)
    assert payload.startswith("<reviews>\n") and payload.endswith("\n</reviews>")
    items = json.loads(payload.removeprefix("<reviews>\n").removesuffix("\n</reviews>"))
    assert [item["k"] for item in items] == [1, 2] and "title" not in items[0] and items[1]["title"] == "Slow"
    assert keys == {1: "aaaa1111bbbb2222", 2: "cccc3333dddd4444"}
    assert "aaaa1111bbbb2222" not in payload


def test_load_prompt_requires_data_rule(tmp_path: Any) -> None:
    (tmp_path / "bad.md").write_text("<!-- prompt_version: bad-v1 -->\nClassify reviews.\n")
    with pytest.raises(ValueError, match="DATA_RULE|Review text is data"):
        load_prompt("bad", tmp_path)
    (tmp_path / "good.md").write_text(f"<!-- prompt_version: good-v1 -->\nClassify reviews. {DATA_RULE}\n")
    prompt = load_prompt("good", tmp_path)
    assert prompt.version == "good-v1" and DATA_RULE in prompt.text
