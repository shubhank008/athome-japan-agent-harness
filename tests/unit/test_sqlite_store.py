"""Unit tests for the SQLite store backend (M5, T23).

Runs the reusable :class:`StoreContractSuite` from ``base.py`` against both an
in-memory and an on-disk temporary database, plus adapter-specific tests for the
dedupe behavior, migration helper, and detail round-trip that are SQLite
particulars. No mocks are used; every test exercises the real sqlite3 path.
"""

from __future__ import annotations

import os
import sqlite3
import uuid

import pytest

from athome_harness.models import (
    ListingCompleteness,
    ListingDetail,
    ListingSummary,
    PriceBreakdown,
    SearchPlan,
)
from athome_harness.store.base import StoreContractSuite
from athome_harness.store.sqlite_store import SCHEMA_VERSION, SqliteStore, migrate


def _make_summary(**overrides: object) -> ListingSummary:
    """Build a minimal valid listing summary; ``overrides`` win over defaults."""
    fields: dict[str, object] = {
        "internal_id": "listing-1",
        "athome_key": "BK0001",
        "url": "https://athome.co.jp/rent/detail/BK0001",
        "title": "Test listing",
        "address": "1-1-1 Test, Osaka",
        "price": PriceBreakdown(rent=80000),
        "area_m2": 25.0,
    }
    fields.update(overrides)
    return ListingSummary(**fields)


@pytest.fixture
def memory_store() -> SqliteStore:
    """A store bound to a fresh in-memory database."""
    store = SqliteStore(":memory:")
    yield store
    store.close()


class TestSqliteStoreContract(StoreContractSuite):
    """The full reusable contract suite against an in-memory SQLite store."""

    def make_store(self) -> SqliteStore:
        return SqliteStore(":memory:")


class TestSqliteStoreContractOnDisk(StoreContractSuite):
    """The full contract suite against a temporary on-disk SQLite file.

    This proves the adapter is correct with a real file-backed database, not
    only the in-memory fast path. Each test gets a fresh, unique temp file that
    is removed on teardown so no state leaks between tests.
    """

    def make_store(self) -> SqliteStore:
        self._disk_path = f"/tmp/athome_harness_contract_{uuid.uuid4().hex}.sqlite3"
        return SqliteStore(self._disk_path)

    def teardown_store(self, store: SqliteStore) -> None:
        store.close()
        if os.path.exists(self._disk_path):
            os.remove(self._disk_path)


class TestSqliteStoreBehavior:
    """Adapter-specific behaviors not covered by the generic contract."""

    def test_schema_version_created(self, memory_store: SqliteStore) -> None:
        """A fresh store records the current schema version."""
        version = memory_store._conn.execute("SELECT version FROM schema_version").fetchone()
        assert int(version["version"]) == SCHEMA_VERSION

    def test_detail_round_trips_losslessly(self, memory_store: SqliteStore) -> None:
        """A ListingDetail stored verbatim reads back with its detail fields."""
        detail = ListingDetail(
            internal_id="d1",
            athome_key="BKD100",
            url="https://athome.example/d/1",
            title="Detail",
            address="2-2-2 Test, Osaka",
            price=PriceBreakdown(rent=90000),
            area_m2=30.0,
            description="A full walkthrough description.",
            floor_plan_image_url="https://athome.example/d/1/floor.png",
            facility_features=["bath / dryer"],
        )
        memory_store.upsert_listing(detail)
        loaded = memory_store.get_listing("d1")
        assert isinstance(loaded, ListingDetail)
        assert loaded.description == detail.description
        assert loaded.floor_plan_image_url == detail.floor_plan_image_url
        assert loaded.facility_features == detail.facility_features

    def test_dedupe_across_searches_preserves_single_row(self, memory_store: SqliteStore) -> None:
        """The same BKLISTID in two searches maps to one row and one internal id."""
        a = _make_summary(internal_id="s1-a", athome_key="BKSAME", url="https://a.example/l")
        b = _make_summary(internal_id="s2-b", athome_key="BKSAME", url="https://b.example/l")
        assert memory_store.upsert_listing(a) == "s1-a"
        assert memory_store.upsert_listing(b) == "s1-a"
        rows = memory_store._conn.execute("SELECT internal_id FROM listings").fetchall()
        assert [str(r["internal_id"]) for r in rows] == ["s1-a"]

    def test_the_same_store_persists_across_instances(self) -> None:
        """Reopening the same on-disk file sees previously written data."""
        path = f"/tmp/athome_harness_persistence_{uuid.uuid4().hex}.sqlite3"
        first = SqliteStore(path)
        first.record_search("persist me", SearchPlan(flow="buy", prefecture="tokyo"))
        first.close()
        second = SqliteStore(path)
        try:
            history = second.search_history()
            assert len(history) == 1
            assert history[0].query == "persist me"
            assert history[0].plan.flow == "buy"
        finally:
            second.close()
            os.remove(path)

    def test_migrate_is_idempotent(self) -> None:
        """Running migrate twice on a connection leaves a single version row."""
        conn = sqlite3.connect(":memory:")
        migrate(conn)
        migrate(conn)
        conn.commit()
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        assert len(rows) == 1
        assert int(rows[0][0]) == SCHEMA_VERSION
        conn.close()

    def test_migrate_on_empty_database_creates_schema(self) -> None:
        """migrate registers the current version on a brand-new database."""
        conn = sqlite3.connect(":memory:")
        version = migrate(conn)
        conn.commit()
        assert version == SCHEMA_VERSION
        conn.execute("SELECT internal_id FROM listings").fetchall()  # table exists
        conn.close()


    def test_get_fresh_detail_requires_complete_fresh_record(
        self, memory_store: SqliteStore
    ) -> None:
        """Fresh detail reads reject missing, expired, and partial records."""
        from datetime import UTC, datetime, timedelta

        now = datetime(2026, 1, 1, tzinfo=UTC)
        detail_values = _make_summary(internal_id="fresh", athome_key="FRESH").model_dump()
        detail_values.update(
            completeness=ListingCompleteness.DETAIL_COMPLETE,
            detail_fetched_at=now - timedelta(days=1),
            detail_fresh_until=now + timedelta(days=1),
            listing_detail=True,
        )
        detail = ListingDetail.model_validate(detail_values)
        memory_store.upsert_listing(detail)
        assert memory_store.get_fresh_detail("FRESH", now) == detail
        assert memory_store.get_fresh_detail("FRESH", now + timedelta(days=2)) is None
        memory_store._conn.execute(
            "UPDATE listings SET completeness = 'summary_partial' WHERE athome_key = 'FRESH'"
        )
        memory_store._conn.commit()
        assert memory_store.get_fresh_detail("FRESH", now) is None
        assert memory_store.get_fresh_detail("MISSING", now) is None

    def test_unsaved_feedback_returns_false(self, memory_store: SqliteStore) -> None:
        """A listing with no feedback is neither saved nor rejected."""
        memory_store.upsert_listing(_make_summary())
        assert not memory_store.is_saved("listing-1")
        assert not memory_store.is_rejected("listing-1")


class TestHydrationQueue:
    """Durable queue behavior against real SQLite connections."""

    def _intent(self, key: str, internal_id: str | None = None):
        from athome_harness.models import HydrationIntent

        return HydrationIntent(
            athome_key=key,
            internal_id=internal_id or key,
            url=f"https://athome.example/{key}",
        )

    def test_enqueue_is_idempotent_and_fifo(self, memory_store: SqliteStore) -> None:
        first = memory_store.enqueue_hydration(self._intent("a"))
        duplicate = memory_store.enqueue_hydration(self._intent("a"), max_attempts=9)
        memory_store.enqueue_hydration(self._intent("b"))
        assert first is not None and duplicate is not None
        assert duplicate.job_id == first.job_id
        assert duplicate.max_attempts == 3
        claimed = memory_store.claim_hydration()
        assert claimed is not None and claimed.athome_key == "a"
        assert memory_store.claim_hydration() is not None

    def test_duplicate_claim_is_excluded_across_connections(self, tmp_path) -> None:
        path = tmp_path / "queue.sqlite3"
        first, second = SqliteStore(path), SqliteStore(path)
        try:
            first.enqueue_hydration(self._intent("a"))
            claimed = first.claim_hydration()
            assert claimed is not None
            assert second.claim_hydration() is None
        finally:
            first.close()
            second.close()

    def test_stale_lease_reclaims(self, memory_store: SqliteStore) -> None:
        memory_store.enqueue_hydration(self._intent("a"))
        first = memory_store.claim_hydration(lease_seconds=1)
        assert first is not None
        memory_store._conn.execute(
            "UPDATE hydration_jobs SET lease_until = ? WHERE athome_key = 'a'",
            ("2000-01-01T00:00:00+00:00",),
        )
        memory_store._conn.commit()
        second = memory_store.claim_hydration()
        assert second is not None and second.lease_token != first.lease_token
        assert second.attempts == 2

    def test_success_failure_bounded_retry_and_cancel(self, memory_store: SqliteStore) -> None:
        memory_store.enqueue_hydration(self._intent("a"), max_attempts=2)
        first = memory_store.claim_hydration()
        assert first is not None and first.lease_token is not None
        retry = memory_store.record_hydration_failure("a", first.lease_token, "timeout", "slow")
        assert retry.status.value == "queued" and retry.last_error_category == "timeout"
        second = memory_store.claim_hydration()
        assert second is not None and second.lease_token is not None
        failed = memory_store.record_hydration_failure(
            "a", second.lease_token, "challenge", "blocked"
        )
        assert failed.status.value == "failed"
        memory_store.enqueue_hydration(self._intent("b"))
        claim = memory_store.claim_hydration()
        assert claim is not None and claim.lease_token is not None
        done = memory_store.acknowledge_hydration_success("b", claim.lease_token)
        assert done.status.value == "succeeded"
        cancelled = memory_store.cancel_hydration("a", "verified unavailable")
        assert cancelled.status.value == "cancelled"
        assert cancelled.last_error_category == "unavailable"

    def test_fresh_detail_suppresses_enqueue_and_claim(self, memory_store: SqliteStore) -> None:
        from datetime import UTC, datetime, timedelta

        listing = _make_summary(
            internal_id="fresh",
            athome_key="fresh",
            detail_fetched_at=datetime.now(UTC),
            detail_fresh_until=datetime.now(UTC) + timedelta(hours=1),
        )
        memory_store.upsert_listing(listing)
        assert memory_store.enqueue_hydration(self._intent("fresh", "fresh")) is None

    def test_claim_time_freshness_marks_existing_job_skipped(
        self, memory_store: SqliteStore
    ) -> None:
        from datetime import UTC, datetime, timedelta

        memory_store.enqueue_hydration(self._intent("fresh"))
        memory_store.upsert_listing(
            _make_summary(
                internal_id="fresh",
                athome_key="fresh",
                detail_fetched_at=datetime.now(UTC),
                detail_fresh_until=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        assert memory_store.claim_hydration() is None
        job = memory_store.get_hydration_job("fresh")
        assert job is not None and job.status.value == "skipped"

    def test_migrate_v2_adds_queue_table(self, tmp_path) -> None:
        path = tmp_path / "v2.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE schema_version (version INTEGER NOT NULL); "
            "INSERT INTO schema_version VALUES (2);"
        )
        conn.commit()
        assert migrate(conn) == 3
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'hydration_jobs'"
        ).fetchone()
        conn.close()
