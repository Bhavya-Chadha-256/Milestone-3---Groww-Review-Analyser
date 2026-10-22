import pytest

from pulse.config import RateLimits
from pulse.llm.ratelimit import RateLimiter, utc_day
from pulse.llm.schema import DailyBudgetExhausted, RequestTooLarge
from pulse.store import Store

MODEL = "openai/gpt-oss-120b"


class FakeClock:
    """A monotonic clock that only moves when the limiter sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_limiter(
    store: Store, limits: RateLimits | None = None, reserve: int = 0
) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    limiter = RateLimiter(
        limits or RateLimits(), store, MODEL, reserve=reserve, clock=clock.time, sleep=clock.sleep
    )
    return limiter, clock


def test_rate_limiter_waits_to_stay_under_tpm(tmp_store: Store) -> None:  # LLM-10
    limiter, clock = make_limiter(tmp_store)
    for _ in range(3):
        limiter.acquire(3000)
    assert clock.sleeps == [pytest.approx(60.0)]


def test_rate_limiter_paces_requests_per_minute(tmp_store: Store) -> None:
    limiter, clock = make_limiter(tmp_store, RateLimits(rpm=2))
    for _ in range(3):
        limiter.acquire(10)
    assert clock.sleeps == [pytest.approx(60.0)]


def test_rate_limiter_honours_retry_after_on_429(tmp_store: Store) -> None:  # LLM-10
    limiter, clock = make_limiter(tmp_store)
    limiter.wait_after_429(2.5)
    limiter.wait_after_429(None)
    assert clock.sleeps == [2.5, 10.0]


def test_daily_budget_exhausted_is_raised_before_sending(tmp_store: Store) -> None:  # LLM-13
    tmp_store.add_llm_usage(utc_day(), MODEL, 196_000)
    limiter, _ = make_limiter(tmp_store)
    with pytest.raises(DailyBudgetExhausted) as raised:
        limiter.acquire(5_000)
    assert (raised.value.used, raised.value.limit) == (196_000, 200_000)


def test_daily_request_limit_is_enforced(tmp_store: Store) -> None:
    limiter, _ = make_limiter(tmp_store, RateLimits(rpd=1))
    limiter.acquire(10)
    limiter.record(10)
    with pytest.raises(DailyBudgetExhausted):
        limiter.acquire(10)


def test_classify_reserve_keeps_tokens_for_later_stages(tmp_store: Store) -> None:
    tmp_store.add_llm_usage(utc_day(), MODEL, 178_000)
    limiter, _ = make_limiter(tmp_store, reserve=20_000)
    with pytest.raises(DailyBudgetExhausted):
        limiter.acquire(5_000, keep_reserve=True)
    limiter.acquire(5_000)


def test_request_larger_than_tpm_is_rejected(tmp_store: Store) -> None:  # LLM-12
    limiter, _ = make_limiter(tmp_store)
    with pytest.raises(RequestTooLarge, match="limits.tpm"):
        limiter.acquire(9_000)


def test_daily_usage_persists_across_clients(tmp_store: Store) -> None:  # OPS-6
    first, _ = make_limiter(tmp_store)
    first.acquire(6000)
    first.record(5000)
    second, _ = make_limiter(tmp_store)
    assert second.remaining_today() == 195_000
    assert tmp_store.llm_usage(utc_day(), MODEL) == (5000, 1)
