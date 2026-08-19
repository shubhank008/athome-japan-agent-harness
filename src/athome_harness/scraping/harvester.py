"""Pagination engine over a page fetch (milestone M6, T24).

The :class:`Harvester` drives the ``harvest`` step of the funnel: it fetches
one results page at a time through an injected synchronous ``fetch_page``
callable, parses each page, and keeps going until the data runs out or a
configured budget (pages or runtime) is hit. Blocks and AtHome challenge pages
fail closed: they never become partial listings and they never silently turn
the run into a "complete" result. The result is a :class:`HarvestResult` that
carries a ``partial`` flag and, when aborted, the abort reason.

Per the repository Abstract First invariant, this module depends only on the
standard library, the project's :class:`Budgets` and :class:`ListingSummary`
shapes, and injected callables. It performs no network I/O of its own; the
``fetch_page`` callable is supplied by the caller so tests exercise real code
paths with a fake transport and production wires in the
:class:`~athome_harness.scraping.session_refarmer.SessionRefarmer` fallback.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from athome_harness.config import Budgets
from athome_harness.models import ListingSummary
from athome_harness.scraping.base import BlockDetected, redact_url
from athome_harness.scraping.challenge import detect_athome_challenge

logger = logging.getLogger(__name__)

# The reasons a harvest may end before all pages are fetched (contract markers).
AbortReason = Literal["pages", "runtime", "block"]

# A page that yields fewer listings than this is treated as the end of data.
_EMPTY_PAGE_THRESHOLD = 0


class HarvestResult(BaseModel):
    """Outcome of one paginated harvest.

    ``listings`` holds every summary gathered across pages (logically
    deduplicated by ``internal_id``); ``pages_scraped`` counts fetched pages.
    ``partial`` is True when a budget or block stopped the loop early;
    ``abort_reason`` records which one, and is ``None`` on a natural end (the
    data ran out) or on a zero-listing first page.
    """

    listings: list[ListingSummary] = Field(
        default_factory=list, description="Summaries gathered across all pages."
    )
    pages_scraped: int = Field(default=0, ge=0, description="Number of pages fetched.")
    partial: bool = Field(
        default=False,
        description="True when a budget or block aborted the loop before completion.",
    )
    abort_reason: AbortReason | None = Field(
        default=None, description="Which budget/block stopped the loop, if any."
    )


class Harvester:
    """Paginate a results feed with per-page budget enforcement.

    ``fetch_page`` returns the HTML of a page URL; ``parse_page`` turns that
    HTML into :class:`ListingSummary` values; ``build_page_url`` maps a 1-based
    page number to its URL. The clock is injectable so runtime-budget tests are
    deterministic. Block and challenge detection fail closed.
    """

    def __init__(
        self,
        *,
        fetch_page: Callable[[str], str],
        parse_page: Callable[[str], list[ListingSummary]],
        build_page_url: Callable[[int], str],
        budgets: Budgets,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Configure the pagination loop around injectable callables."""
        self._fetch_page = fetch_page
        self._parse_page = parse_page
        self._build_page_url = build_page_url
        self._budgets = budgets
        # Wrap the injectable clock so every instance has a monotonic source.
        self._clock: Callable[[], float] = clock or time.monotonic

    def harvest(self, *, expected_pages: int = 0) -> HarvestResult:
        """Fetch and parse pages until the data or a budget runs out.

        Emits the ``[HARVEST_START]``, ``[HARVEST_PAGE]``, ``[BUDGET_HIT]``,
        ``[PARTIAL_REPORT]``, and ``[HARVEST_DONE]`` contract markers with the
        exact payload keys the marker contract requires. A page that returns no
        listings ends the loop naturally (``partial=False``); hitting the page
        or runtime budget, a block, or an AtHome challenge sets ``partial=True``
        with the matching ``abort_reason``.
        """
        max_pages = max(1, self._budgets.max_pages)
        logger.info("[HARVEST_START] expected_pages=%d max_pages=%d", expected_pages, max_pages)
        started = self._clock()
        listing_by_id: dict[str, ListingSummary] = {}
        pages = 0
        partial = False
        reason: AbortReason | None = None

        for page in range(1, max_pages + 1):
            if self._runtime_exceeded(started):
                partial, reason = True, "runtime"
                self._log_budget_hit("runtime")
                break
            url = self._build_page_url(page)
            page_started = self._clock()
            try:
                html = self._fetch_page(url)
            except BlockDetected as block:
                # The BlockDetected constructor already logs [BLOCK_DETECTED].
                del block
                reason = "block"
                partial = True
                logger.warning(
                    "[PARTIAL_REPORT] reason=block listings=%d",
                    len(listing_by_id),
                )
                break
            except Exception as exc:  # noqa: BLE001 - any fetch failure aborts cleanly
                # A transport failure that is not a typed block also aborts the
                # harvest as a block-class degradation so the run is bounded.
                reason = "block"
                partial = True
                logger.warning(
                    "[PARTIAL_REPORT] reason=block listings=%d error=<%s>",
                    len(listing_by_id),
                    type(exc).__name__,
                )
                break

            # Fail closed on a challenge page: it is not listing content and must
            # never be parsed or surfaced as results.
            challenge = detect_athome_challenge(html)
            if challenge is not None:
                partial = True
                reason = "block"
                logger.warning(
                    "[ATHOME_CHALLENGE] url=<%s> kind=<%s> htmlLength=<%d>",
                    redact_url(url),
                    challenge,
                    len(html),
                )
                logger.warning("[PARTIAL_REPORT] reason=block listings=%d", len(listing_by_id))
                break

            page_summaries = self._parse_page(html)
            pages += 1
            for summary in page_summaries:
                listing_by_id[summary.internal_id] = summary
            elapsed = self._clock() - page_started
            logger.info(
                "[HARVEST_PAGE] page=%d listings=%d elapsed_s=%.3f",
                page,
                len(listing_by_id),
                elapsed,
            )
            if len(page_summaries) <= _EMPTY_PAGE_THRESHOLD:
                break
            if page >= max_pages:
                partial = True
                reason = "pages"
                self._log_budget_hit("pages")

        if reason == "pages":
            logger.warning("[PARTIAL_REPORT] reason=budget listings=%d", len(listing_by_id))
        logger.info(
            "[HARVEST_DONE] pages=%d listings=%d partial=%s",
            pages,
            len(listing_by_id),
            str(partial).lower(),
        )
        return HarvestResult(
            listings=list(listing_by_id.values()),
            pages_scraped=pages,
            partial=partial,
            abort_reason=reason,
        )

    def _runtime_exceeded(self, started: float) -> bool:
        """Return True when the session runtime budget is exhausted."""
        limit_s = self._budgets.runtime_minutes * 60.0
        return (self._clock() - started) >= limit_s

    def _log_budget_hit(self, kind: Literal["pages", "runtime"]) -> None:
        """Emit the ``[BUDGET_HIT]`` marker for the given budget kind."""
        limit = (
            self._budgets.runtime_minutes * 60.0
            if kind == "runtime"
            else max(1, self._budgets.max_pages)
        )
        logger.warning("[BUDGET_HIT] kind=%s limit=%s", kind, limit)
