"""A scripted stand-in for GroqLLM, so stage tests never call the network (Task 3.1)."""

from collections.abc import Callable
from types import SimpleNamespace

from pydantic import BaseModel

from pulse.llm.schema import DailyBudgetExhausted, LLMRequest, UsageLedger

Handler = Callable[[LLMRequest], BaseModel] | list[BaseModel | Exception]


class FakeLLM:
    def __init__(  # craftsman-ignore: WARN-PY001 (test double with optional knobs)
        self,
        handlers: dict[str, Handler],
        served_model: str = "openai/gpt-oss-120b",
        tokens_left: int = 10**9,
        tokens_per_call: int = 1000,
    ) -> None:
        self.handlers = handlers
        self.served_model = served_model
        self.tokens_left = tokens_left
        self.tokens_per_call = tokens_per_call
        self.calls: list[LLMRequest] = []
        self.usage = UsageLedger()

    def remaining_tokens_today(self, model: str | None = None) -> int:
        return self.tokens_left

    def structured(self, req: LLMRequest) -> BaseModel:
        if self.tokens_left < self.tokens_per_call:
            raise DailyBudgetExhausted(10**9 - self.tokens_left, 10**9)
        self.tokens_left -= self.tokens_per_call
        self.calls.append(req)
        handler = self.handlers[req.stage]
        if callable(handler):
            result = handler(req)
        else:
            item = handler.pop(0) if len(handler) > 1 else handler[0]
            if isinstance(item, Exception):
                raise item
            result = item
        usage = SimpleNamespace(
            prompt_tokens=self.tokens_per_call // 2, completion_tokens=self.tokens_per_call // 2
        )
        self.usage.add(req.stage, usage, model=self.served_model)
        return result
