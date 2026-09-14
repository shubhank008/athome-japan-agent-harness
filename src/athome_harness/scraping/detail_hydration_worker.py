"""Lean, config-gated worker for durable detail hydration jobs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from athome_harness.models import Agency, HydrationJob, ListingCompleteness, ListingDetail
from athome_harness.scraping.base import BaseScraper, BlockDetected, redact_url
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.rate_limiter import TokenBucketRateLimiter
from athome_harness.scraping.server_app_state import extract_server_app_agency

logger = logging.getLogger(__name__)
DETAIL_FRESH_DAYS = 14
_UNAVAILABLE_MARKERS = ("掲載終了", "募集終了", "この物件は削除されました")


class HydrationStore(Protocol):
    """Minimal injected store surface used by the worker."""

    def claim_hydration(self, lease_seconds: int = 300) -> HydrationJob | None: ...

    def get_listing(self, internal_id: str) -> object | None: ...

    def upsert_listing(self, listing: ListingDetail) -> str: ...

    def upsert_agency(self, agency: Agency) -> str: ...

    def acknowledge_hydration_success(self, athome_key: str, lease_token: str) -> HydrationJob: ...

    def record_hydration_failure(
        self, athome_key: str, lease_token: str, category: str, error: str
    ) -> HydrationJob: ...

    def cancel_hydration(self, athome_key: str, reason: str) -> HydrationJob: ...


class HydrationStop(Exception):
    """Signal that the current worker run must stop after a challenge or block."""


@dataclass(frozen=True)
class WorkerReport:
    """Operator-safe result of one bounded worker run."""

    claimed: int
    succeeded: int
    skipped: int
    retried: int
    cancelled: int
    stopped: bool


class DetailHydrationWorker:
    """Process FIFO hydration jobs through claim, fetch, parse, and acknowledge."""

    def __init__(
        self,
        store: HydrationStore,
        fetch: Callable[[str], str] | BaseScraper,
        *,
        limiter: TokenBucketRateLimiter,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        parser: Callable[[str], ListingDetail] = parse_detail_page,
        max_jobs: int = 1,
        lease_seconds: int = 300,
        idle_sleep_seconds: float = 30.0,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        self._store = store
        self._fetch = fetch.fetch_html if isinstance(fetch, BaseScraper) else fetch
        self._limiter = limiter
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or time.sleep
        self._parser = parser
        self._max_jobs = max_jobs
        self._lease_seconds = lease_seconds
        self._idle_sleep_seconds = idle_sleep_seconds

    def run_once(self) -> WorkerReport:
        """Process at most one claimed job."""
        return self._run(1)

    def run_loop(self, *, max_jobs: int | None = None) -> WorkerReport:
        """Process a bounded number of jobs, sleeping between empty polls."""
        return self._run(max_jobs or self._max_jobs)

    def _run(self, limit: int) -> WorkerReport:
        claimed = succeeded = skipped = retried = cancelled = 0
        stopped = False
        for index in range(limit):
            job = self._store.claim_hydration(lease_seconds=self._lease_seconds)
            if job is None:
                skipped += 1
                if limit > 1 and index + 1 < limit:
                    self._sleeper(self._idle_sleep_seconds)
                break
            claimed += 1
            try:
                result = self._process(job)
                succeeded += result == "succeeded"
                cancelled += result == "cancelled"
            except HydrationStop:
                stopped = True
                break
            except RetryableHydrationError:
                retried += 1
            if index + 1 < limit and not stopped:
                self._sleeper(self._idle_sleep_seconds)
        logger.info(
            "[HYDRATION_WORKER_REPORT] claimed=%d succeeded=%d skipped=%d "
            "retried=%d cancelled=%d stopped=%s",
            claimed, succeeded, skipped, retried, cancelled, stopped,
        )
        return WorkerReport(claimed, succeeded, skipped, retried, cancelled, stopped)

    def _process(self, job: HydrationJob) -> str:
        """Run exactly one claimed job and guard every durable transition."""
        assert job.lease_token is not None
        safe_url = redact_url(job.url)
        try:
            self._limiter.acquire()
            html = self._fetch(job.url)
            challenge = detect_athome_challenge(html)
            if challenge is not None:
                self._failure(job, "challenge", f"challenge={challenge}")
                logger.warning("[HYDRATION_CHALLENGE_STOP] url=<%s> kind=<%s>", safe_url, challenge)
                raise HydrationStop
            unavailable = next((marker for marker in _UNAVAILABLE_MARKERS if marker in html), None)
            if unavailable is not None:
                self._store.cancel_hydration(job.athome_key, f"selector-marker={unavailable}")
                logger.info("[HYDRATION_CANCELLED_UNAVAILABLE] key=<%s>", job.athome_key)
                return "cancelled"
            detail = self._parser(html)
            if detail.athome_key != job.athome_key or detail.internal_id != job.internal_id:
                self._failure(job, "identity", "detail identity did not match claimed job")
                return "failed"
            agency = extract_server_app_agency(html)
            if agency is not None:
                self._store.upsert_agency(agency)
                detail = detail.model_copy(update={"agency": agency})
            now = self._clock()
            detail = detail.model_copy(
                update={
                    "completeness": ListingCompleteness.DETAIL_COMPLETE,
                    "listing_detail": True,
                    "detail_fetched_at": now,
                    "detail_fresh_until": now + timedelta(days=DETAIL_FRESH_DAYS),
                }
            )
            self._store.upsert_listing(detail)
            self._store.acknowledge_hydration_success(job.athome_key, job.lease_token)
            logger.info("[HYDRATION_SUCCEEDED] key=<%s>", job.athome_key)
            return "succeeded"
        except BlockDetected as exc:
            self._failure(job, "block", f"signature={exc.signature}")
            logger.warning(
                "[HYDRATION_BLOCK_STOP] url=<%s> signature=<%s>", safe_url, exc.signature
            )
            raise HydrationStop from exc
        except HydrationStop:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            self._failure(job, "transient", type(exc).__name__)
            raise RetryableHydrationError from exc
        except Exception as exc:
            self._failure(job, "parser", type(exc).__name__)
            return "failed"

    def _failure(self, job: HydrationJob, category: str, error: str) -> None:
        """Record a categorized failure without source HTML or credentials."""
        assert job.lease_token is not None
        self._store.record_hydration_failure(job.athome_key, job.lease_token, category, error)
        logger.warning(
            "[HYDRATION_FAILURE] key=<%s> category=<%s> source=<%s>",
            job.athome_key,
            category,
            redact_url(job.url),
        )


class RetryableHydrationError(Exception):
    """Internal signal used to count a retryable failure."""
