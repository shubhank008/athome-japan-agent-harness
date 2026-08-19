"""Data store abstraction and reusable contract (milestone M5, T22).

This module owns the business-facing persistence contract
(:class:`BaseDataStore`), the value objects returned by history queries
(:class:`SearchRecord`, :class:`RecommendationRecord`), and the reusable
contract test suite (:class:`StoreContractSuite`) that every future backend
(such as the SQLite adapter in ``sqlite_store.py``) must satisfy.

It follows the repository's Abstract First invariant: only the standard library
and the project's own models are imported here. No third-party database or
serialization library appears in the interface. Because the abstract interface
is backend-agnostic, swapping SQLite for a remote store later is a drop-in
change for the rest of the harness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field

from athome_harness.models import ListingSummary, PriceBreakdown, Recommendation, SearchPlan

__all__ = [
    "BaseDataStore",
    "SearchRecord",
    "RecommendationRecord",
    "StoreContractSuite",
    "FEEDBACK_SAVE",
    "FEEDBACK_REJECT",
]

# Feedback action identifiers stored in the feedback table.
FEEDBACK_SAVE = "save"
FEEDBACK_REJECT = "reject"


class SearchRecord(BaseModel):
    """One recorded search, as returned by :meth:`BaseDataStore.search_history`.

    ``search_id`` is a backend-owned identifier; ``query`` and ``plan`` capture
    what was actually executed so a returning user sees what ran before.
    """

    search_id: int = Field(description="Backend-owned identifier for the search.")
    query: str = Field(description="Original natural-language query.")
    plan: SearchPlan = Field(description="The interpreted search plan that ran.")
    created_at: str = Field(description="ISO-8601 timestamp when the search was recorded.")


class RecommendationRecord(BaseModel):
    """One recorded recommendation, as returned by history queries.

    Wraps the domain :class:`Recommendation` together with the backend-owned
    ``search_id`` that produced it and the timestamp, so callers can replay or
    audit what was surfaced to the user.
    """

    search_id: int = Field(description="Identifier of the search that produced it.")
    recommendation: Recommendation = Field(description="The recorded recommendation.")
    created_at: str = Field(description="ISO-8601 timestamp when it was recorded.")


class BaseDataStore(ABC):
    """Abstract persistence contract implemented by concrete backends.

    The store owns the session memory described in US-005: every listing maps to
    a stable internal property ID (deduplicated across searches by the AtHome
    ``BKLISTID`` key and canonical URL), and searches, recommendations, saves,
    and rejections all persist so the harness never repeats what the user has
    already seen, saved, or rejected. It also exposes a small generic
    ``cache_meta`` key-value surface that the US-009 prefetch worker uses for
    freshness and TTL bookkeeping.

    Implementations must be safe to construct on a throwaway location (an
    in-memory database) so tests can exercise the full contract without touching
    the user's on-disk store.
    """

    # -- Listing ingestion ---------------------------------------------------

    @abstractmethod
    def upsert_listing(self, listing: ListingSummary) -> str:
        """Persist ``listing`` and return its canonical internal ID.

        Listings are keyed by the stable internal ID, while the AtHome
        ``BKLISTID`` and canonical URL are deduplicated: if a listing with the
        same key or URL already exists, the existing row is updated in place and
        its existing internal ID is returned, so no duplicate property row is
        ever created across searches.

        Because :class:`ListingDetail` extends :class:`ListingSummary`, a detail
        object may be passed here; extra fields are preserved on readback when
        possible.
        """

    @abstractmethod
    def get_listing(self, internal_id: str) -> ListingSummary | None:
        """Return the listing with ``internal_id``, or ``None`` if absent."""

    @abstractmethod
    def list_listings(self) -> list[ListingSummary]:
        """Return every persisted listing in insertion order."""

    # -- Searches ------------------------------------------------------------

    @abstractmethod
    def record_search(self, query: str, plan: SearchPlan) -> int:
        """Record one search and return its backend-owned ``search_id``."""

    @abstractmethod
    def search_history(self, limit: int = 20) -> list[SearchRecord]:
        """Return recent searches, newest first, capped at ``limit``."""

    # -- Recommendations -----------------------------------------------------

    @abstractmethod
    def record_recommendation(self, search_id: int, recommendations: list[Recommendation]) -> None:
        """Record the ``recommendations`` produced by the given ``search_id``."""

    @abstractmethod
    def recommendation_history(self, limit: int = 50) -> list[RecommendationRecord]:
        """Return recently recorded recommendations, newest first (``limit``)."""

    # -- Feedback (save / reject) -------------------------------------------

    @abstractmethod
    def save_listing(self, internal_id: str) -> None:
        """Mark the listing with ``internal_id`` as saved (latest decision wins)."""

    @abstractmethod
    def reject_listing(self, internal_id: str) -> None:
        """Mark the listing with ``internal_id`` as rejected (latest wins)."""

    @abstractmethod
    def is_saved(self, internal_id: str) -> bool:
        """Return whether the listing is currently marked saved."""

    @abstractmethod
    def is_rejected(self, internal_id: str) -> bool:
        """Return whether the listing is currently marked rejected."""

    @abstractmethod
    def saved_internal_ids(self) -> set[str]:
        """Return the set of internal IDs currently marked saved."""

    @abstractmethod
    def rejected_internal_ids(self) -> set[str]:
        """Return the set of internal IDs currently marked rejected."""

    @abstractmethod
    def seen_internal_ids(self) -> set[str]:
        """Return every internal ID ever persisted (the session's seen set)."""

    @abstractmethod
    def clear_feedback(self, internal_id: str) -> None:
        """Remove any save/reject decision for ``internal_id``."""

    # -- cache_meta (US-009 prefetch freshness) -----------------------------

    @abstractmethod
    def set_cache_meta(self, key: str, value: str | int | float) -> None:
        """Set a generic ``cache_meta`` entry, overwriting any prior value."""

    @abstractmethod
    def get_cache_meta(self, key: str) -> str | int | float | None:
        """Return the ``cache_meta`` value for ``key``, or ``None`` if absent."""

    # -- Lifecycle -----------------------------------------------------------

    @abstractmethod
    def close(self) -> None:
        """Release backend resources (connections, files).

        The store must be unusable after this is called. It is safe to call
        multiple times.
        """


def _new_listing(**overrides: object) -> ListingSummary:
    """Build a minimal valid :class:`ListingSummary` for the contract suite.

    ``overrides`` win over the defaults so tests can vary identity fields.
    """
    fields: dict[str, Any] = {
        "internal_id": "listing-1",
        "athome_key": "BK0001",
        "url": "https://athome.co.jp/rent/detail/BK0001",
        "title": "Contract test listing",
        "address": "1-1-1 Test, Osaka",
        "price": PriceBreakdown(rent=80000),
        "area_m2": 25.0,
    }
    fields.update(overrides)
    return ListingSummary(**fields)


class StoreContractSuite:
    """Reusable behavioral contract for every :class:`BaseDataStore` backend.

    Subclass this in a backend's test module and override :meth:`store` to yield
    a freshly constructed, empty store (typically on a temporary database). Every
    test here must pass for any conformant backend, so a future adapter only has
    to add a fixture to inherit the full suite.
    """

    # Backends override these instance methods with a fresh store and its
    # teardown. ``teardown_store`` is called even on test failure.
    def make_store(self) -> BaseDataStore:
        """Return a fresh, empty store. Subclasses must override this."""
        raise NotImplementedError

    def teardown_store(self, store: BaseDataStore) -> None:
        """Release ``store`` after a test. Defaults to closing it."""
        store.close()

    @contextmanager
    def _store(self) -> Iterator[BaseDataStore]:
        store = self.make_store()
        try:
            yield store
        finally:
            self.teardown_store(store)

    # -- Listings ------------------------------------------------------------

    def test_upsert_returns_internal_id(self) -> None:
        """Upserting a listing returns its canonical internal ID."""
        with self._store() as store:
            listing = _new_listing()
            assert store.upsert_listing(listing) == "listing-1"
            assert store.get_listing("listing-1") == listing

    def test_upsert_deduplicates_by_athome_key(self) -> None:
        """The same BKLISTID maps to one internal row, never a duplicate."""
        with self._store() as store:
            first = _new_listing(internal_id="a", athome_key="BK100")
            second = _new_listing(
                internal_id="b", athome_key="BK100", url="https://other.example/x"
            )
            assert store.upsert_listing(first) == "a"
            # Same BKLISTID, different internal id and URL: dedupes to 'a'.
            assert store.upsert_listing(second) == "a"
            assert store.get_listing("a") is not None
            assert store.get_listing("b") is None
            assert len(store.list_listings()) == 1

    def test_upsert_deduplicates_by_url(self) -> None:
        """The same canonical URL maps to one internal row even with new BKLISTID."""
        with self._store() as store:
            first = _new_listing(
                internal_id="a", athome_key="BK200", url="https://athome.example/l"
            )
            second = _new_listing(
                internal_id="b", athome_key="BK201", url="https://athome.example/l"
            )
            assert store.upsert_listing(first) == "a"
            assert store.upsert_listing(second) == "a"
            assert len(store.list_listings()) == 1

    def test_upsert_updates_existing_row_in_place(self) -> None:
        """Re-upserting an existing key refreshes the payload, not the row count."""
        with self._store() as store:
            original = _new_listing(internal_id="a", athome_key="BK300", title="Old title")
            store.upsert_listing(original)
            updated = _new_listing(internal_id="a", athome_key="BK300", title="New title")
            assert store.upsert_listing(updated) == "a"
            assert len(store.list_listings()) == 1
            loaded = store.get_listing("a")
            assert loaded is not None
            assert loaded.title == "New title"

    def test_get_returns_none_for_unknown(self) -> None:
        """Getting an unknown internal ID returns None."""
        with self._store() as store:
            assert store.get_listing("missing") is None

    def test_list_listings_in_insertion_order(self) -> None:
        """list_listings returns all persisted listings in insertion order."""
        with self._store() as store:
            a = _new_listing(internal_id="a")
            b = _new_listing(internal_id="b", athome_key="BK2", url="https://x/2")
            store.upsert_listing(a)
            store.upsert_listing(b)
            ids = [listing.internal_id for listing in store.list_listings()]
            assert ids == ["a", "b"]

    # -- Searches ------------------------------------------------------------

    def test_record_search_returns_id_and_history(self) -> None:
        """record_search returns a positive id and search_history returns it."""
        with self._store() as store:
            plan = SearchPlan(flow="rent", prefecture="osaka")
            search_id = store.record_search("two bedroom", plan)
            assert search_id >= 1
            history = store.search_history()
            assert len(history) == 1
            record = history[0]
            assert record.search_id == search_id
            assert record.query == "two bedroom"
            assert record.plan.flow == "rent"

    def test_search_history_newest_first_and_limited(self) -> None:
        """search_history orders newest first and honours the limit."""
        with self._store() as store:
            for i in range(3):
                store.record_search(f"query {i}", SearchPlan(flow="rent", prefecture="osaka"))
            histories = store.search_history(limit=2)
            assert [h.query for h in histories] == ["query 2", "query 1"]
            assert len(store.search_history()) == 3

    # -- Recommendations -----------------------------------------------------

    def test_record_and_read_recommendation(self) -> None:
        """Recommendations recorded against a search are read back intact."""
        with self._store() as store:
            listing = _new_listing()
            store.upsert_listing(listing)
            search_id = store.record_search("q", SearchPlan(flow="rent", prefecture="osaka"))
            rec = Recommendation(
                listing_id=listing.internal_id,
                rank=1,
                reasons=["cheap"],
                satisfied_constraints=["rent"],
                violated_constraints=[],
            )
            store.record_recommendation(search_id, [rec])
            records = store.recommendation_history()
            assert len(records) == 1
            assert records[0].search_id == search_id
            assert records[0].recommendation.listing_id == listing.internal_id
            assert records[0].recommendation.rank == 1

    # -- Feedback ------------------------------------------------------------

    def test_save_and_reject_are_exclusive_latest_wins(self) -> None:
        """save and reject are mutually exclusive; the latest decision wins."""
        with self._store() as store:
            store.upsert_listing(_new_listing())
            assert not store.is_saved("listing-1")
            assert not store.is_rejected("listing-1")
            store.save_listing("listing-1")
            assert store.is_saved("listing-1")
            assert not store.is_rejected("listing-1")
            # Rejecting overrides the earlier save.
            store.reject_listing("listing-1")
            assert store.is_rejected("listing-1")
            assert not store.is_saved("listing-1")

    def test_saved_and_rejected_id_sets(self) -> None:
        """saved/rejected id sets reflect the current decisions."""
        with self._store() as store:
            store.upsert_listing(_new_listing(internal_id="a"))
            store.upsert_listing(_new_listing(internal_id="b", athome_key="BK2", url="https://x/2"))
            store.save_listing("a")
            store.reject_listing("b")
            assert store.saved_internal_ids() == {"a"}
            assert store.rejected_internal_ids() == {"b"}

    def test_seen_internal_ids_covers_all_persisted(self) -> None:
        """seen_internal_ids returns every persisted listing id."""
        with self._store() as store:
            store.upsert_listing(_new_listing(internal_id="a"))
            store.upsert_listing(_new_listing(internal_id="b", athome_key="BK2", url="https://x/2"))
            assert store.seen_internal_ids() == {"a", "b"}

    def test_clear_feedback_resets_decision(self) -> None:
        """clear_feedback removes a save/reject decision."""
        with self._store() as store:
            store.upsert_listing(_new_listing())
            store.save_listing("listing-1")
            store.clear_feedback("listing-1")
            assert not store.is_saved("listing-1")
            assert not store.is_rejected("listing-1")

    # -- cache_meta (US-009) -------------------------------------------------

    def test_cache_meta_round_trip(self) -> None:
        """set_cache_meta stores values; get_cache_meta retrieves them by key."""
        with self._store() as store:
            assert store.get_cache_meta("missing") is None
            store.set_cache_meta("fetched_at", "2026-07-08T00:00:00Z")
            assert store.get_cache_meta("fetched_at") == "2026-07-08T00:00:00Z"
            store.set_cache_meta("ttl_hours", 48)
            assert store.get_cache_meta("ttl_hours") == 48

    def test_cache_meta_overwrite(self) -> None:
        """set_cache_meta overwrites a prior value for the same key."""
        with self._store() as store:
            store.set_cache_meta("k", "first")
            store.set_cache_meta("k", "second")
            assert store.get_cache_meta("k") == "second"
