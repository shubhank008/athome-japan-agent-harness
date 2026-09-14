"""Real-code tests for the bounded detail hydration worker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from athome_harness.models import HydrationIntent, ListingDetail, ListingSummary, PriceBreakdown
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.detail_hydration_worker import DetailHydrationWorker
from athome_harness.store.sqlite_store import SqliteStore


class NoWaitLimiter:
    """Injected limiter that records acquisitions without delaying tests."""

    def __init__(self) -> None:
        self.calls = 0

    def acquire(self) -> float:
        """Record one permitted request."""
        self.calls += 1
        return 0.0


def detail(key: str) -> ListingDetail:
    """Build a valid parsed detail for a claimed key."""
    return ListingDetail(
        internal_id=key,
        athome_key=key,
        url=f"https://www.athome.co.jp/chintai/{key}/",
        title="Hydrated",
        address="Osaka",
        price=PriceBreakdown(rent=80000),
        area_m2=25.0,
    )


def worker(store: SqliteStore, fetch, parser, *, sleeps=None, max_jobs=1) -> DetailHydrationWorker:
    """Construct a worker with all timing and parsing boundaries injected."""
    return DetailHydrationWorker(
        store,
        fetch,
        limiter=NoWaitLimiter(),
        parser=parser,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        sleeper=(sleeps if sleeps is not None else []).append,
        max_jobs=max_jobs,
        idle_sleep_seconds=2.0,
    )


def enqueue(store: SqliteStore, key: str = "A") -> None:
    """Insert one durable queue job."""
    store.enqueue_hydration(
        HydrationIntent(
            athome_key=key,
            internal_id=key,
            url=f"https://user:secret@athome.example/detail/{key}?token=secret",
        )
    )


def test_success_persists_fresh_detail_and_acknowledges() -> None:
    """A valid detail follows fetch, parse, upsert, and acknowledgement."""
    store = SqliteStore(":memory:")
    enqueue(store)
    report = worker(store, lambda _: "listing", lambda _: detail("A")).run_once()
    saved = store.get_listing("A")
    assert report.succeeded == 1
    assert saved is not None and saved.detail_fresh_until is not None
    assert (saved.detail_fresh_until - saved.detail_fetched_at).days == 14
    assert store.get_hydration_job("A").status.value == "succeeded"


def test_identity_mismatch_retries_without_persisting() -> None:
    """A parsed response for another listing is categorized as identity failure."""
    store = SqliteStore(":memory:")
    enqueue(store)
    report = worker(store, lambda _: "listing", lambda _: detail("OTHER")).run_once()
    job = store.get_hydration_job("A")
    assert report.succeeded == 0 and report.claimed == 1
    assert job is not None and job.last_error_category == "identity"
    assert store.get_listing("A") is None


def test_challenge_stops_run_and_never_fetches_next_job() -> None:
    """A challenge is recorded and stops a bounded multi-job run."""
    store = SqliteStore(":memory:")
    enqueue(store, "A")
    enqueue(store, "B")
    report = worker(
        store, lambda _: "Click to verify", lambda _: detail("A"), max_jobs=2
    ).run_loop()
    assert report.stopped and report.claimed == 1
    assert store.get_hydration_job("A").last_error_category == "challenge"
    assert store.get_hydration_job("B").status.value == "queued"


def test_block_stops_and_redacts_source(caplog: pytest.LogCaptureFixture) -> None:
    """Block reporting never includes credentials or query strings."""
    store = SqliteStore(":memory:")
    enqueue(store)

    def blocked(_: str) -> str:
        raise BlockDetected("https://user:secret@athome.example/a?token=secret", "403")

    report = worker(store, blocked, lambda _: detail("A")).run_once()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert report.stopped
    assert "secret" not in messages and "?token" not in messages
    assert "[HYDRATION_BLOCK_STOP]" in messages


def test_transient_error_requeues() -> None:
    """A timeout uses the queue retry transition and remains queued."""
    store = SqliteStore(":memory:")
    enqueue(store)
    report = worker(
        store, lambda _: (_ for _ in ()).throw(TimeoutError()), lambda _: detail("A")
    ).run_once()
    job = store.get_hydration_job("A")
    assert report.retried == 1
    assert job is not None
    assert job.status.value == "queued" and job.last_error_category == "transient"


def test_unavailable_marker_cancels_only_positive_selector() -> None:
    """Only the explicit unavailable marker invokes cancellation."""
    store = SqliteStore(":memory:")
    enqueue(store)
    report = worker(store, lambda _: "掲載終了", lambda _: detail("A")).run_once()
    assert report.cancelled == 1
    assert store.get_hydration_job("A").status.value == "cancelled"


def test_fresh_claim_is_skipped_without_fetch() -> None:
    """T32 claim freshness suppresses fetching before the worker starts."""
    store = SqliteStore(":memory:")
    store.upsert_listing(
        ListingSummary(
            internal_id="A", athome_key="A", url="https://athome.example/A",
            title="Existing", address="Osaka", price=PriceBreakdown(rent=80000), area_m2=25.0,
        )
    )
    enqueue(store)
    store._conn.execute(
        "UPDATE listings SET detail_fresh_until = ? WHERE internal_id = ?",
        ("2999-01-01T00:00:00+00:00", "A"),
    )
    store._conn.commit()
    calls: list[str] = []
    report = worker(store, lambda url: calls.append(url) or "bad", lambda _: detail("A")).run_once()
    assert report.skipped == 1 and calls == []


def test_loop_sleeps_after_empty_poll() -> None:
    """Bounded loop mode sleeps between empty polls instead of busy spinning."""
    store = SqliteStore(":memory:")
    sleeps: list[float] = []
    report = worker(
        store, lambda _: "unused", lambda _: detail("A"), sleeps=sleeps, max_jobs=2
    ).run_loop()
    assert report.skipped == 1 and sleeps == [2.0]
