"""Deterministic unit tests for the token-bucket rate limiter (T06).

A fake clock, a scripted RNG, and a clock-jumping sleeper let the tests prove
spacing between ``acquire()`` calls and jitter bounds without wall-clock sleeps.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

from athome_harness.config import Budgets
from athome_harness.scraping.rate_limiter import TokenBucketRateLimiter


class FakeClock:
    """Monotonic seconds clock that only advances when told to (by the sleeper)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# Used as the default budget in helpers below; kept module-level so it is created
# once instead of at every call (avoids B008).
_DEFAULT_BUDGETS = Budgets()


def build_limiter(
    budgets: Budgets | None = None,
    *,
    rng_values: list[float] | None = None,
) -> tuple[TokenBucketRateLimiter, FakeClock, list[float]]:
    """Build a limiter whose jitter draws sequentially from ``rng_values``.

    Returns the limiter, the fake clock, and a record of every sleep requested.
    When ``rng_values`` is ``None`` the RNG always returns 0.0 (no jitter).
    """
    clock = FakeClock()
    sleeps: list[float] = []
    rng = _scripted_rng(rng_values or [0.0])

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    limiter = TokenBucketRateLimiter(
        budgets or _DEFAULT_BUDGETS, clock=clock, rng=rng, sleeper=sleeper
    )
    return limiter, clock, sleeps


def _scripted_rng(values: list[float]) -> Callable[[], float]:
    """Return a callable that yields ``values`` cyclically as draws."""

    iterator = itertools.cycle(values)

    def draw() -> float:
        return next(iterator)

    return draw


def test_burst_calls_within_budget_do_not_sleep() -> None:
    """The first ``rate_requests`` calls are under the bucket capacity and sleep 0."""
    limiter, _, sleeps = build_limiter(Budgets(rate_requests=3))
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == 0.0
    assert sleeps == []


def test_call_beyond_capacity_waits_for_refill() -> None:
    """A call past the burst capacity pays the base interval and stays bounded."""
    limiter, _, sleeps = build_limiter(Budgets(rate_requests=1, rate_interval_s=2.0))
    assert limiter.acquire() == 0.0
    waited = limiter.acquire()
    assert waited == 2.0  # 1 token / interval base wait, no jitter
    assert sleeps == [2.0]


def test_jitter_is_bounded_by_budget() -> None:
    """The jitter portion of a wait never exceeds rate_jitter_max_s."""
    limiter, clock, sleeps = build_limiter(
        Budgets(rate_requests=1, rate_interval_s=2.0, rate_jitter_max_s=1.0),
        rng_values=[0.5],
    )
    # Drain the bucket so the next acquire must wait.
    assert limiter.acquire() == 0.0
    waited = limiter.acquire()
    # Base = 2.0s, jitter = 0.5 * 1.0 = 0.5s.
    assert waited == 2.5
    assert sleeps == [2.5]


def test_jitter_minimum_and_maximum() -> None:
    """Jitter of 0.0 and 1.0 map to the extremes of the allowed range."""
    for rng_value, expected_jitter in ((0.0, 0.0), (1.0, 1.0)):
        limiter, _, sleeps = build_limiter(
            Budgets(rate_requests=1, rate_interval_s=1.0, rate_jitter_max_s=1.0),
            rng_values=[rng_value],
        )
        limiter.acquire()  # drain
        waited = limiter.acquire()
        # base wait 1.0 + jitter; assert only the jitter portion.
        assert waited - 1.0 == expected_jitter
        assert sleeps == [1.0 + expected_jitter]


def test_spacing_between_acquires_never_below_base() -> None:
    """Consecutive acquires are at least interval/requests apart (base wait)."""
    budgets = Budgets(rate_requests=2, rate_interval_s=4.0, rate_jitter_max_s=1.0)
    limiter, _, _ = build_limiter(budgets, rng_values=[0.0, 1.0, 0.5])
    waits = [limiter.acquire() for _ in range(5)]
    base = budgets.rate_interval_s / budgets.rate_requests  # 2.0
    # The first two calls are within capacity (wait 0); every later wait >= base.
    assert waits[0] == 0.0
    assert waits[1] == 0.0
    for wait in waits[2:]:
        assert base <= wait <= base + budgets.rate_jitter_max_s


def test_determinism_with_fixed_rng() -> None:
    """The same budget, clock, and RNG script produce identical spacing."""
    first, _, _ = build_limiter(Budgets(), rng_values=[0.3, 0.7])
    second, _, _ = build_limiter(Budgets(), rng_values=[0.3, 0.7])
    assert [first.acquire() for _ in range(3)] == [second.acquire() for _ in range(3)]
