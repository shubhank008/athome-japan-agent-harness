"""Proxy rotation policy and abstract provider (T09).

The rotation policy is: try the direct connection first, engage a proxy only
when a block is reported, rotate through the configured pool one proxy per
consecutive block, and stop after ``Budgets.proxy_retries`` proxy attempts.
:class:`BaseProxyProvider` turns that policy into a reusable state machine and
concrete providers (Webshare first) only supply the candidate pool.

Per the Abstract First invariant this module imports only the standard library
and the project's own config/interfaces. No third-party HTTP library appears
here; HTTP transport lives in the adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from athome_harness.config import Budgets
from athome_harness.scraping.base import ProxyProvider

__all__ = ["BaseProxyProvider", "ProxyProvider"]


class BaseProxyProvider(ABC):
    """Direct-first rotating proxy provider with a bounded retry budget.

    Concrete subclasses implement :meth:`_build_pool` to return the candidate
    proxy URLs for one session. The base class keeps rotation state and enforces
    ``Budgets.proxy_retries``: every call to :meth:`report_block` advances to
    the next candidate and returns ``None`` once the budget is exhausted so the
    HTTP adapter stops retrying and surfaces :class:`BlockDetected`.

    The provider is stateless across sessions: build a fresh instance per search
    session so proxy usage does not leak between unrelated runs.
    """

    def __init__(self, budgets: Budgets) -> None:
        self._budgets = budgets
        self._pool = self._build_pool()
        self._retries_used = 0

    @abstractmethod
    def _build_pool(self) -> list[str]:
        """Return the candidate proxy URLs (with credentials) for one session."""

    def get_proxy(self) -> str | None:
        """Return the current proxy URL, or ``None`` to keep a direct connection.

        Direct-first semantics: before the first block, ``None`` (direct) is
        returned; after a block, the currently active candidate is returned.
        When the pool is exhausted but the budget allows further attempts, the
        last candidate is kept active so retries stay within the retry budget.
        """
        if self._retries_used == 0 or not self._pool:
            return None
        index = min(self._retries_used, len(self._pool)) - 1
        return self._pool[index]

    def report_block(self, url: str) -> str | None:
        """Record a block on ``url`` and rotate, or ``None`` when budget is spent.

        Each consecutive block consumes one proxy attempt up to
        ``Budgets.proxy_retries``. Once the budget is exhausted (including the
        empty-pool case) this returns ``None`` so callers stop retrying. When
        the pool is shorter than the budget, the last candidate is reused for
        the remaining attempts.
        """
        if self._retries_used >= self._budgets.proxy_retries or not self._pool:
            return None
        self._retries_used += 1
        index = min(self._retries_used, len(self._pool)) - 1
        return self._pool[index]

    def reset(self) -> None:
        """Re-arm the rotation counter for a new session."""
        self._retries_used = 0
