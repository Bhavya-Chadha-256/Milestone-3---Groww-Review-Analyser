"""Client-side pacing for the provider's per-minute and per-day limits (architecture.md §5.1, LLM-10, LLM-13).

The per-minute window counts each request's prompt estimate plus its output allowance, because Groq
charges the allowance against tokens-per-minute up front. Daily usage is persisted in the store, so a
restarted process still knows what the day has used (OPS-6).
"""

import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from pulse.config import RateLimits
from pulse.llm.schema import DailyBudgetExhausted, LLMError
from pulse.store import Store

WINDOW_SECONDS = 60.0
DEFAULT_RETRY_SECONDS = 10.0


def utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


class RateLimiter:
    def __init__(  # craftsman-ignore: WARN-PY001 (clock and sleep are injected for tests)
        self,
        limits: RateLimits,
        store: Store,
        model: str,
        *,
        reserve: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] = utc_day,
    ) -> None:
        self.limits = limits
        self.store = store
        self.model = model
        self.reserve = reserve
        self.clock = clock
        self.sleep = sleep
        self.today = today
        self.window: deque[tuple[float, int]] = deque()

    def remaining_today(self, *, keep_reserve: bool = False) -> int:
        used, _ = self.store.llm_usage(self.today(), self.model)
        return max(0, self._daily_limit(keep_reserve) - used)

    def acquire(self, tokens: int, *, keep_reserve: bool = False) -> None:
        """Wait until `tokens` fit the per-minute window; raise before sending if the day is used up."""
        if tokens > self.limits.tpm:
            raise LLMError(
                f"a {tokens}-token request can never fit limits.tpm ({self.limits.tpm}); send less per call"
            )
        used, requests = self.store.llm_usage(self.today(), self.model)
        limit = self._daily_limit(keep_reserve)
        if used + tokens > limit or requests + 1 > self.limits.rpd:
            raise DailyBudgetExhausted(used, limit)
        while not self._fits(tokens):
            self.sleep(max(0.05, self.window[0][0] + WINDOW_SECONDS - self.clock()))
        self.window.append((self.clock(), tokens))

    def record(self, used_tokens: int) -> None:
        self.store.add_llm_usage(self.today(), self.model, used_tokens)

    def wait_after_429(self, retry_after: float | None) -> None:
        self.sleep(retry_after if retry_after is not None else DEFAULT_RETRY_SECONDS)

    def _daily_limit(self, keep_reserve: bool) -> int:
        return self.limits.tpd - (self.reserve if keep_reserve else 0)

    def _fits(self, tokens: int) -> bool:
        now = self.clock()
        while self.window and now - self.window[0][0] >= WINDOW_SECONDS:
            self.window.popleft()
        in_window = sum(spent for _, spent in self.window)
        return in_window + tokens <= self.limits.tpm and len(self.window) < self.limits.rpm
