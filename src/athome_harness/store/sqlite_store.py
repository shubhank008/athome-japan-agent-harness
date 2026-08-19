"""SQLite persistence backend (milestone M5, T23).

Implements :class:`BaseDataStore` over a SQLite database using schema version 1.
Listings are stored keyed by an internal property ID while the AtHome
``BKLISTID`` and canonical URL are deduplicated through unique constraints, so
the same property never appears twice across searches (US-005). Searches,
recommendations, saves, rejects, and a generic ``cache_meta`` table (used by the
US-009 prefetch worker) round out the persistence surface.

The schema is created idempotently and versioned. :func:`migrate` upgrades an
existing database to the current schema version, which keeps this module
compatible with databases created by earlier versions of the harness. Tests
exercise the full :class:`StoreContractSuite` plus specific adapter behavior
against temporary in-memory and on-disk databases.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from athome_harness.models import ListingDetail, ListingSummary, Recommendation, SearchPlan
from athome_harness.store.base import (
    FEEDBACK_REJECT,
    FEEDBACK_SAVE,
    BaseDataStore,
    RecommendationRecord,
    SearchRecord,
)

# Current on-disk schema version. Bump this (and add a step to `migrate`) when
# the table DDL changes.
SCHEMA_VERSION = 1

# The full set of cache_meta value types the store preserves and returns.
_CacheValue = str | int | float

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    internal_id   TEXT PRIMARY KEY,
    athome_key    TEXT NOT NULL,
    url           TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (athome_key),
    UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id          INTEGER NOT NULL REFERENCES searches (id),
    listing_id         TEXT NOT NULL,
    rank               INTEGER NOT NULL,
    reasons_json       TEXT NOT NULL,
    satisfied_json     TEXT NOT NULL,
    violated_json      TEXT NOT NULL,
    probable_neg_json  TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    internal_id   TEXT PRIMARY KEY,
    action        TEXT NOT NULL CHECK (action IN ('save', 'reject')),
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_meta (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version  INTEGER NOT NULL
);
"""


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def migrate(connection: sqlite3.Connection) -> int:
    """Bring ``connection`` up to the current schema version.

    Creates the schema if no version is recorded, otherwise applies missing
    migrations in order. Returns the resulting schema version. The caller owns
    committing the transaction.
    """
    connection.executescript(_SCHEMA_SQL)
    version = _read_version(connection)
    if version is None:
        _exec(connection, "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        return SCHEMA_VERSION
    if version < SCHEMA_VERSION:
        # Future migrations append steps here, e.g.
        #   if version < 2:
        #       _exec(connection, "ALTER TABLE listings ADD COLUMN ...")
        #       version = 2
        #   ...
        _exec(connection, "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    return SCHEMA_VERSION


def _read_version(connection: sqlite3.Connection) -> int | None:
    """Return the recorded schema version, or None when no version row exists."""
    try:
        row = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return int(row[0])


def _exec(connection: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> None:
    """Execute ``sql`` with ``params`` on ``connection``."""
    connection.execute(sql, params)


def _serialize_listing(listing: ListingSummary) -> str:
    """Serialize a listing (summary or detail) to its stored JSON payload."""
    return listing.model_dump_json()


def _deserialize_listing(payload: str) -> ListingSummary:
    """Rebuild a listing from its stored JSON payload.

    Because :class:`ListingDetail` extends :class:`ListingSummary`, detail fields
    are preserved on readback when present so a detail round-trip is lossless.
    """
    raw = json.loads(payload)
    if "description" in raw:
        return ListingDetail.model_validate(raw)
    return ListingSummary.model_validate(raw)


def _serialize_cache_value(value: str | int | float) -> str:
    """Serialize a cache_meta value with an explicit type tag."""
    return json.dumps({"t": type(value).__name__, "v": value})


def _deserialize_cache_value(payload: str) -> str | int | float:
    """Rebuild a cache_meta value from its tagged JSON payload."""
    raw = json.loads(payload)
    return cast("str | int | float", raw["v"])


class SqliteStore(BaseDataStore):
    """A :class:`BaseDataStore` backed by a single SQLite database file.

    ``path`` may be a filesystem path or the special string ``":memory:"`` (used
    by tests). The schema is created and migrated to the current version on
    construction. A single connection is opened lazily on first use and reused
    for the lifetime of the store.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._connection: sqlite3.Connection | None = None

    @property
    def _conn(self) -> sqlite3.Connection:
        """Return the lazily opened connection, migrating the schema on first use."""
        if self._connection is None:
            connection = sqlite3.connect(self._path)
            connection.row_factory = sqlite3.Row
            migrate(connection)
            connection.commit()
            self._connection = connection
        return self._connection

    def close(self) -> None:
        """Close the underlying connection, if open. Safe to call repeatedly."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # -- Listings ------------------------------------------------------------

    def upsert_listing(self, listing: ListingSummary) -> str:
        """Persist ``listing`` and return its canonical internal ID.

        The internal ID is keyed and the ``athome_key`` / ``url`` are unique, so
        a property previously stored under any of those identities is updated in
        place and its existing internal ID is returned (dedupe across searches).
        """
        conn = self._conn
        now = _now()
        # Resolve dedupe against existing athome_key or url.
        existing = conn.execute(
            "SELECT internal_id FROM listings WHERE athome_key = ? OR url = ?",
            (listing.athome_key, listing.url),
        ).fetchone()
        if existing is not None:
            canonical = str(existing["internal_id"])
            conn.execute(
                "UPDATE listings SET athome_key = ?, url = ?, payload = ?, updated_at = ? "
                "WHERE internal_id = ?",
                (listing.athome_key, listing.url, _serialize_listing(listing), now, canonical),
            )
            conn.commit()
            return canonical
        conn.execute(
            "INSERT INTO listings (internal_id, athome_key, url, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                listing.internal_id,
                listing.athome_key,
                listing.url,
                _serialize_listing(listing),
                now,
                now,
            ),
        )
        conn.commit()
        return listing.internal_id

    def get_listing(self, internal_id: str) -> ListingSummary | None:
        """Return the listing with ``internal_id``, or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT payload FROM listings WHERE internal_id = ?", (internal_id,)
        ).fetchone()
        if row is None:
            return None
        return _deserialize_listing(str(row["payload"]))

    def list_listings(self) -> list[ListingSummary]:
        """Return every persisted listing in insertion order."""
        rows = self._conn.execute(
            "SELECT payload FROM listings ORDER BY created_at, internal_id"
        ).fetchall()
        return [_deserialize_listing(str(row["payload"])) for row in rows]

    # -- Searches ------------------------------------------------------------

    def record_search(self, query: str, plan: SearchPlan) -> int:
        """Record one search and return its backend-owned ``search_id``."""
        conn = self._conn
        now = _now()
        cursor = conn.execute(
            "INSERT INTO searches (query, plan_json, created_at) VALUES (?, ?, ?)",
            (query, plan.model_dump_json(), now),
        )
        conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # an INSERT always fills lastrowid
        return int(last_id)

    def search_history(self, limit: int = 20) -> list[SearchRecord]:
        """Return recent searches, newest first, capped at ``limit``."""
        rows = self._conn.execute(
            "SELECT id, query, plan_json, created_at FROM searches ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SearchRecord(
                search_id=int(row["id"]),
                query=str(row["query"]),
                plan=SearchPlan.model_validate_json(str(row["plan_json"])),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    # -- Recommendations -----------------------------------------------------

    def record_recommendation(self, search_id: int, recommendations: list[Recommendation]) -> None:
        """Record the ``recommendations`` produced by the given ``search_id``."""
        conn = self._conn
        now = _now()
        for rec in recommendations:
            conn.execute(
                "INSERT INTO recommendations "
                "(search_id, listing_id, rank, reasons_json, satisfied_json, "
                " violated_json, probable_neg_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    search_id,
                    rec.listing_id,
                    rec.rank,
                    json.dumps(rec.reasons),
                    json.dumps(rec.satisfied_constraints),
                    json.dumps(rec.violated_constraints),
                    json.dumps(rec.probable_negatives),
                    now,
                ),
            )
        conn.commit()

    def recommendation_history(self, limit: int = 50) -> list[RecommendationRecord]:
        """Return recently recorded recommendations, newest first (``limit``)."""
        rows = self._conn.execute(
            "SELECT search_id, listing_id, rank, reasons_json, satisfied_json, "
            "       violated_json, probable_neg_json, created_at "
            "FROM recommendations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            RecommendationRecord(
                search_id=int(row["search_id"]),
                recommendation=Recommendation(
                    listing_id=str(row["listing_id"]),
                    rank=int(row["rank"]),
                    reasons=json.loads(str(row["reasons_json"])),
                    satisfied_constraints=json.loads(str(row["satisfied_json"])),
                    violated_constraints=json.loads(str(row["violated_json"])),
                    probable_negatives=json.loads(str(row["probable_neg_json"])),
                ),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    # -- Feedback ------------------------------------------------------------

    def _set_feedback(self, internal_id: str, action: str) -> None:
        """Upsert the current feedback action for a listing (latest wins)."""
        conn = self._conn
        now = _now()
        conn.execute(
            "INSERT INTO feedback (internal_id, action, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (internal_id) DO UPDATE SET action = excluded.action, "
            "updated_at = excluded.updated_at",
            (internal_id, action, now),
        )
        conn.commit()

    def save_listing(self, internal_id: str) -> None:
        """Mark the listing as saved (latest decision wins)."""
        self._set_feedback(internal_id, FEEDBACK_SAVE)

    def reject_listing(self, internal_id: str) -> None:
        """Mark the listing as rejected (latest decision wins)."""
        self._set_feedback(internal_id, FEEDBACK_REJECT)

    def is_saved(self, internal_id: str) -> bool:
        """Return whether the listing is currently marked saved."""
        return self._feedback_action(internal_id) == FEEDBACK_SAVE

    def is_rejected(self, internal_id: str) -> bool:
        """Return whether the listing is currently marked rejected."""
        return self._feedback_action(internal_id) == FEEDBACK_REJECT

    def _feedback_action(self, internal_id: str) -> str | None:
        """Return the current feedback action for a listing, or None."""
        row = self._conn.execute(
            "SELECT action FROM feedback WHERE internal_id = ?", (internal_id,)
        ).fetchone()
        if row is None:
            return None
        return str(row["action"])

    def saved_internal_ids(self) -> set[str]:
        """Return the set of internal IDs currently marked saved."""
        return self._feedback_ids(FEEDBACK_SAVE)

    def rejected_internal_ids(self) -> set[str]:
        """Return the set of internal IDs currently marked rejected."""
        return self._feedback_ids(FEEDBACK_REJECT)

    def _feedback_ids(self, action: str) -> set[str]:
        """Return the internal IDs currently carrying ``action``."""
        rows = self._conn.execute(
            "SELECT internal_id FROM feedback WHERE action = ?", (action,)
        ).fetchall()
        return {str(row["internal_id"]) for row in rows}

    def seen_internal_ids(self) -> set[str]:
        """Return every internal ID ever persisted (the session's seen set)."""
        rows = self._conn.execute("SELECT internal_id FROM listings").fetchall()
        return {str(row["internal_id"]) for row in rows}

    def clear_feedback(self, internal_id: str) -> None:
        """Remove any save/reject decision for ``internal_id``."""
        self._conn.execute("DELETE FROM feedback WHERE internal_id = ?", (internal_id,))
        self._conn.commit()

    # -- cache_meta (US-009) -------------------------------------------------

    def set_cache_meta(self, key: str, value: str | int | float) -> None:
        """Set a generic ``cache_meta`` entry, overwriting any prior value."""
        conn = self._conn
        conn.execute(
            "INSERT INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, _serialize_cache_value(value), _now()),
        )
        conn.commit()

    def get_cache_meta(self, key: str) -> str | int | float | None:
        """Return the ``cache_meta`` value for ``key``, or ``None`` if absent."""
        row = self._conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return _deserialize_cache_value(str(row["value"]))
