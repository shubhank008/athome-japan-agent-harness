"""Token-bucket rate limiter with random jitter (T06).

The limiter enforces the ``Budgets.rate_requests`` per ``Budgets.rate_interval_s``
politeness window and adds a random delay in ``0..rate_jitter_max_s`` on top of
the base wait so requests spread out like a polite browser. The clock, the random
generator, and the sleeper are injectable so unit tests prove spacing and jitter
bounds deterministically without sleeping.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

from athome_harness.config import Budgets


class TokenBucketRateLimiter:
    """A lock-protected token bucket driven by ``Budgets``.

    Tokens refill continuously at ``rate_requests / rate_interval_s`` per second
    up to a capacity of ``rate_requests``. Each :meth:`acquire` consumes one
    token; when the bucket is empty it sleeps for the base wait to refill one
    token plus a jitter drawn uniformly from ``[0, rate_jitter_max_s]``.

    All inputs are injectable: ``clock`` (monotonic seconds), ``rng`` (uniform
    [0, 1)), and ``sleeper`` (the blocking sleep). This keeps tests
    deterministic, fast, and free of wall-clock dependence.
    """

    def __init__(
        self,
        budgets: Budgets,
        *,
        clock: Callable[[], float] | None = None,
        rng: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._budgets = budgets
        self._clock: Callable[[], float] = clock or time.monotonic
        self._rng: Callable[[], float] = rng or random.random
        self._sleeper: Callable[[float], None] = sleeper or time.sleep
        self._lock = threading.Lock()
        # Capacity equals the burst allowed per interval.
        self._capacity = max(1.0, float(budgets.rate_requests))
        self._tokens = self._capacity
        self._last = self._clock()

    def acquire(self) -> float:
        """Block until a request may fire; return the seconds slept (0.0 if under limit).

        The returned value is the total wall-clock-equivalent delay imposed,
        which is the base refill wait plus the jitter. Tests assert on it to
        prove inter-request spacing and jitter bounds.
        """
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            # Bucket is empty: pay the refill wait plus random jitter.
            wait = self._base_wait()
            jitter = self._rng() * self._budgets.rate_jitter_max_s
            total = wait + jitter
            self._sleeper(total)
            # The refilled token is consumed by this request.
            self._tokens = 0.0
            self._last = self._clock()
            return total

    def _refill(self) -> None:
        """Accumulate tokens proportional to elapsed time since the last request."""
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        rate = self._budgets.rate_requests / self._budgets.rate_interval_s
        self._tokens = min(self._capacity, self._tokens + elapsed * rate)
        self._last = now

    def _base_wait(self) -> float:
        """Seconds needed to refill one token from an empty bucket."""
        if self._budgets.rate_requests <= 0:
            return 0.0
        return self._budgets.rate_interval_s / self._budgets.rate_requests
