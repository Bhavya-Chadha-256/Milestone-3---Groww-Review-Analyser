"""Groq chat-completions client with strict structured outputs (architecture.md §5, Task 3.1)."""

import json
import os
import time
from collections.abc import Callable
from typing import Any

import groq
from pydantic import BaseModel, ValidationError

from pulse.config import LLMSettings
from pulse.llm.ratelimit import RateLimiter, utc_day
from pulse.llm.schema import (
    LLMError,
    LLMRefusal,
    LLMRequest,
    LLMSchemaError,
    LLMTruncated,
    UsageLedger,
    request_tokens,
    wire_schema,
)
from pulse.store import Store

REASONING_MODEL_PREFIX = "openai/gpt-oss"  # only these models accept reasoning_effort
RATE_LIMIT_ATTEMPTS = 3
DAILY_LIMIT_RETRY_SECONDS = 60.0  # a longer retry-after means the provider's daily quota is used up
REASK_SUFFIX = "\n\nYour previous output failed validation: {error}. Return corrected JSON only."


class InvalidOutput(Exception):
    """The response didn't match the schema; `structured` re-asks once."""


def build_params(req: LLMRequest, settings: LLMSettings) -> dict[str, Any]:
    stage = settings.stages[req.stage]
    model = req.model or settings.model
    params: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
        "max_completion_tokens": stage.max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": req.schema.__name__, "strict": True, "schema": wire_schema(req.schema)},
        },
    }
    if model.startswith(REASONING_MODEL_PREFIX):
        params["reasoning_effort"] = stage.effort
        params["include_reasoning"] = False
    return params


def parse_response(resp: Any, schema: type[BaseModel]) -> tuple[BaseModel, str]:
    choice = resp.choices[0]
    if choice.finish_reason == "content_filter":
        raise LLMRefusal("content_filter")
    if choice.finish_reason == "length":
        raise LLMTruncated("response hit max_completion_tokens")
    try:
        return schema.model_validate_json(choice.message.content or ""), resp.model
    except ValidationError as error:
        raise InvalidOutput(str(error)) from error


class GroqLLM:
    def __init__(  # craftsman-ignore: WARN-PY001 (client, clock and sleep are injected for tests)
        self,
        settings: LLMSettings,
        store: Store,
        client: Any = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] = utc_day,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client or self._default_client(settings)
        self.clock, self.sleep, self.today = clock, sleep, today
        self.limiters: dict[str, RateLimiter] = {}
        self.usage = UsageLedger()

    @staticmethod
    def _default_client(settings: LLMSettings) -> Any:
        key = os.environ.get(settings.api_key_env)
        if not key:
            raise LLMError(f"{settings.api_key_env} is not set; add it to .env (see .env.example)")
        return groq.Groq(api_key=key, max_retries=0)  # retries go through the limiter, so they are counted

    def remaining_tokens_today(self, model: str | None = None) -> int:
        return self._limiter(model or self.settings.model).remaining_today()

    def structured(self, req: LLMRequest) -> BaseModel:
        params = build_params(req, self.settings)
        try:
            return self._send(req, params)
        except InvalidOutput as error:
            retry = dict(params)
            retry["messages"] = [
                params["messages"][0],
                {"role": "user", "content": req.user + REASK_SUFFIX.format(error=str(error)[:500])},
            ]
            try:
                return self._send(req, retry)
            except InvalidOutput as second:
                raise LLMSchemaError(str(second)[:500]) from second

    def _limiter(self, model: str) -> RateLimiter:
        if model not in self.limiters:
            self.limiters[model] = RateLimiter(
                self.settings.limits,
                self.store,
                model,
                reserve=self.settings.daily_token_reserve,
                clock=self.clock,
                sleep=self.sleep,
                today=self.today,
            )
        return self.limiters[model]

    def _send(self, req: LLMRequest, params: dict[str, Any]) -> BaseModel:
        limiter = self._limiter(params["model"])
        tokens = request_tokens(req, self.settings.stages[req.stage])
        problem = "rate-limited"
        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            limiter.acquire(tokens, keep_reserve=req.stage == "classify")
            last = attempt == RATE_LIMIT_ATTEMPTS
            try:
                resp = self.client.chat.completions.create(**params)
            except groq.RateLimitError as error:
                retry_after = _retry_after(error)
                if _daily_limit_reached(error, retry_after):  # LLM-13: resume tomorrow, don't sleep for hours
                    raise limiter.day_used_up() from error
                problem = "rate-limited"
                if not last:
                    limiter.wait_after_429(retry_after)
                continue
            except (groq.InternalServerError, groq.APIConnectionError):
                problem = "unavailable"
                if not last:
                    limiter.wait_after_429(None)
                continue
            except groq.BadRequestError as error:
                if "json_validate_failed" in str(error) or "json_validate_failed" in json.dumps(error.body):
                    limiter.record(tokens)  # the rejected output still used tokens; count the estimate
                    raise InvalidOutput(str(error)) from error
                raise LLMError(f"bad request: {error}") from error
            limiter.record(resp.usage.total_tokens)
            self.usage.add(req.stage, resp.usage, model=resp.model)
            parsed, _ = parse_response(resp, req.schema)
            return parsed
        raise LLMError(f"still {problem} after {RATE_LIMIT_ATTEMPTS} attempts")


def _daily_limit_reached(error: groq.RateLimitError, retry_after: float | None) -> bool:
    long_wait = retry_after is not None and retry_after > DAILY_LIMIT_RETRY_SECONDS
    return long_wait or "per day" in str(error).lower()


def _retry_after(error: groq.RateLimitError) -> float | None:
    value = error.response.headers.get("retry-after") if error.response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
