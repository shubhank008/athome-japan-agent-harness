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

from athome_harness.models import ListingDetail, ListingSummary, PriceBreakdown, SearchPlan
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

    def test_unsaved_feedback_returns_false(self, memory_store: SqliteStore) -> None:
        """A listing with no feedback is neither saved nor rejected."""
        memory_store.upsert_listing(_make_summary())
        assert not memory_store.is_saved("listing-1")
        assert not memory_store.is_rejected("listing-1")
