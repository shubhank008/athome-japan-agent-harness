"""SQLite persistence backend (milestone M5, T23).

Implements :class:`BaseDataStore` over a SQLite database using schema version 3.
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
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from athome_harness.models import (
    Agency,
    HydrationIntent,
    HydrationJob,
    HydrationStatus,
    ListingCompleteness,
    ListingDetail,
    ListingSummary,
    Recommendation,
    SearchPlan,
)
from athome_harness.store.base import (
    FEEDBACK_REJECT,
    FEEDBACK_SAVE,
    BaseDataStore,
    RecommendationRecord,
    SearchRecord,
)

# Current on-disk schema version. Bump this (and add a step to `migrate`) when
# the table DDL changes.
SCHEMA_VERSION = 3

# The full set of cache_meta value types the store preserves and returns.
_CacheValue = str | int | float

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    internal_id   TEXT PRIMARY KEY,
    athome_key    TEXT NOT NULL,
    url           TEXT NOT NULL,
    payload       TEXT NOT NULL,
    completeness  TEXT NOT NULL DEFAULT 'summary_partial',
    detail_fetched_at TEXT,
    detail_fresh_until TEXT,
    agency_kaiin_no TEXT REFERENCES agencies (kaiin_no),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (athome_key),
    UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS agencies (
    kaiin_no      TEXT PRIMARY KEY,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS hydration_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athome_key TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    internal_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'leased', 'succeeded', 'skipped', 'failed', 'cancelled'
    )),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lease_token TEXT,
    lease_until TEXT,
    last_error TEXT,
    last_error_category TEXT
);
CREATE INDEX IF NOT EXISTS hydration_jobs_eligible_idx ON hydration_jobs (status, created_at);
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
    if version < 2:
        # Schema v1 stored lifecycle state only inside the JSON payload. These
        # nullable columns make freshness and agency links queryable without
        # invalidating existing rows or payloads.
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(listings)")}
        if "completeness" not in columns:
            _exec(
                connection,
                "ALTER TABLE listings ADD COLUMN completeness TEXT NOT NULL "
                "DEFAULT 'summary_partial'",
            )
        if "detail_fetched_at" not in columns:
            _exec(connection, "ALTER TABLE listings ADD COLUMN detail_fetched_at TEXT")
        if "detail_fresh_until" not in columns:
            _exec(connection, "ALTER TABLE listings ADD COLUMN detail_fresh_until TEXT")
        if "agency_kaiin_no" not in columns:
            _exec(connection, "ALTER TABLE listings ADD COLUMN agency_kaiin_no TEXT")
        _exec(
            connection,
            "CREATE TABLE IF NOT EXISTS agencies ("
            "kaiin_no TEXT PRIMARY KEY, payload TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        )
        version = 2
    if version < 3:
        _exec(
            connection,
            "CREATE TABLE IF NOT EXISTS hydration_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, athome_key TEXT NOT NULL UNIQUE, "
            "url TEXT NOT NULL, internal_id TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN ("
            "'queued', 'leased', 'succeeded', 'skipped', 'failed', 'cancelled')), "
            "attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, lease_token TEXT, "
            "lease_until TEXT, last_error TEXT, last_error_category TEXT)",
        )
        _exec(
            connection,
            "CREATE INDEX IF NOT EXISTS hydration_jobs_eligible_idx "
            "ON hydration_jobs (status, created_at)",
        )
        version = 3
    if version < SCHEMA_VERSION:
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


def _merge_optional(existing: object, incoming: object) -> object:
    """Prefer a meaningful incoming value while retaining richer existing data."""
    if incoming is None or incoming == "" or incoming == [] or incoming == {}:
        return existing
    return incoming


def _merge_agency(existing: Agency | None, incoming: Agency) -> Agency:
    """Merge an agency update without allowing sparse state to erase fields."""
    if existing is None:
        return incoming
    values = existing.model_dump()
    for name, value in incoming.model_dump().items():
        values[name] = _merge_optional(values.get(name), value)
    return Agency.model_validate(values)


def _merge_listing(existing: ListingSummary, incoming: ListingSummary) -> ListingSummary:
    """Merge listing updates without downgrading lifecycle or useful fields."""
    if (
        existing.completeness is ListingCompleteness.DETAIL_COMPLETE
        and incoming.completeness is not ListingCompleteness.DETAIL_COMPLETE
    ):
        return existing
    values = existing.model_dump()
    for name, value in incoming.model_dump().items():
        values[name] = _merge_optional(values.get(name), value)
    lifecycle_rank = {
        ListingCompleteness.SUMMARY_PARTIAL: 0,
        ListingCompleteness.SUMMARY_COMPLETE: 1,
        ListingCompleteness.DETAIL_COMPLETE: 2,
    }
    values["completeness"] = max(
        (existing.completeness, incoming.completeness),
        key=lambda state: lifecycle_rank[state],
    )
    values["detail_fetched_at"] = incoming.detail_fetched_at or existing.detail_fetched_at
    values["detail_fresh_until"] = incoming.detail_fresh_until or existing.detail_fresh_until
    if existing.completeness is ListingCompleteness.DETAIL_COMPLETE:
        values["price"] = existing.price
    if (
        incoming.agency is not None
        and existing.completeness is not ListingCompleteness.DETAIL_COMPLETE
    ):
        values["agency"] = incoming.agency
    if isinstance(existing, ListingDetail) or isinstance(incoming, ListingDetail):
        return ListingDetail.model_validate(values)
    return ListingSummary.model_validate(values)


def _parse_time(value: str | None) -> datetime | None:
    """Parse a stored UTC timestamp, preserving nullable lease fields."""
    return datetime.fromisoformat(value) if value is not None else None


def _job_from_row(row: sqlite3.Row) -> HydrationJob:
    """Convert a hydration row into its public typed model."""
    return HydrationJob(
        job_id=int(row["id"]),
        athome_key=str(row["athome_key"]),
        url=str(row["url"]),
        internal_id=str(row["internal_id"]),
        status=HydrationStatus(str(row["status"])),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        created_at=_parse_time(str(row["created_at"])) or datetime.now(UTC),
        updated_at=_parse_time(str(row["updated_at"])) or datetime.now(UTC),
        lease_token=row["lease_token"],
        lease_until=_parse_time(str(row["lease_until"])) if row["lease_until"] else None,
        last_error=row["last_error"],
        last_error_category=row["last_error_category"],
    )


def _deserialize_cache_value(payload: str) -> str | int | float:
    """Rebuild a cache_meta value from its tagged JSON payload.

    The type tag written by :func:`_serialize_cache_value` is used to restore
    the declared Python type so that ``int`` values survive the JSON round-trip
    even if the storage column is ``TEXT``.
    """
    raw = json.loads(payload)
    tag = raw.get("t")
    value = raw["v"]
    if tag == "int":
        return int(value)
    if tag == "float":
        return float(value)
    if tag == "str":
        return str(value)
    return cast("str | int | float", value)


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
        if listing.agency is not None:
            self.upsert_agency(listing.agency)
        # Resolve dedupe against existing athome_key or url.
        existing = conn.execute(
            "SELECT internal_id, payload FROM listings WHERE athome_key = ? OR url = ?",
            (listing.athome_key, listing.url),
        ).fetchone()
        if existing is not None:
            canonical = str(existing["internal_id"])
            previous = _deserialize_listing(str(existing["payload"]))
            listing = _merge_listing(previous, listing)
            try:
                conn.execute(
                    "UPDATE listings SET athome_key = ?, url = ?, payload = ?, completeness = ?, "
                    "detail_fetched_at = ?, detail_fresh_until = ?, agency_kaiin_no = ?, "
                    "updated_at = ? "
                    "WHERE internal_id = ?",
                    (
                        listing.athome_key,
                        listing.url,
                        _serialize_listing(listing),
                        listing.completeness.value,
                        (
                            listing.detail_fetched_at.isoformat()
                            if listing.detail_fetched_at
                            else None
                        ),
                        (
                            listing.detail_fresh_until.isoformat()
                            if listing.detail_fresh_until
                            else None
                        ),
                        listing.agency.kaiin_no if listing.agency else None,
                        now,
                        canonical,
                    ),
                )
            except sqlite3.IntegrityError:
                # The new athome_key belongs to a different row; merge into that
                # row so the conflicting key's internal ID stays canonical.
                conflicting = conn.execute(
                    "SELECT internal_id FROM listings WHERE athome_key = ?",
                    (listing.athome_key,),
                ).fetchone()
                if conflicting is not None:
                    canonical = str(conflicting["internal_id"])
                conn.execute(
                    "UPDATE listings SET url = ?, payload = ?, completeness = ?, "
                    "detail_fetched_at = ?, detail_fresh_until = ?, agency_kaiin_no = ?, "
                    "updated_at = ? WHERE internal_id = ?",
                    (
                        listing.url,
                        _serialize_listing(listing),
                        listing.completeness.value,
                        (
                            listing.detail_fetched_at.isoformat()
                            if listing.detail_fetched_at
                            else None
                        ),
                        (
                            listing.detail_fresh_until.isoformat()
                            if listing.detail_fresh_until
                            else None
                        ),
                        listing.agency.kaiin_no if listing.agency else None,
                        now,
                        canonical,
                    ),
                )
            conn.commit()
            return canonical
        conn.execute(
            "INSERT INTO listings (internal_id, athome_key, url, payload, completeness, "
            "detail_fetched_at, detail_fresh_until, agency_kaiin_no, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                listing.internal_id,
                listing.athome_key,
                listing.url,
                _serialize_listing(listing),
                listing.completeness.value,
                listing.detail_fetched_at.isoformat() if listing.detail_fetched_at else None,
                listing.detail_fresh_until.isoformat() if listing.detail_fresh_until else None,
                listing.agency.kaiin_no if listing.agency else None,
                now,
                now,
            ),
        )
        conn.commit()
        return listing.internal_id

    def get_listing(self, internal_id: str) -> ListingSummary | None:
        """Return the listing with ``internal_id``, or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT payload, agency_kaiin_no FROM listings WHERE internal_id = ?", (internal_id,)
        ).fetchone()
        if row is None:
            return None
        return self._listing_from_row(row)

    def list_listings(self) -> list[ListingSummary]:
        """Return every persisted listing in insertion order."""
        rows = self._conn.execute(
            "SELECT payload, agency_kaiin_no FROM listings ORDER BY created_at, internal_id"
        ).fetchall()
        return [self._listing_from_row(row) for row in rows]

    def _listing_from_row(self, row: sqlite3.Row) -> ListingSummary:
        """Deserialize a listing row and hydrate its separately stored agency."""
        listing = _deserialize_listing(str(row["payload"]))
        kaiin_no = row["agency_kaiin_no"]
        if kaiin_no is not None:
            agency = self.get_agency(str(kaiin_no))
            if agency is not None:
                listing = listing.model_copy(update={"agency": agency})
        return listing

    # -- Detail hydration queue ---------------------------------------------

    def _fresh_detail(self, internal_id: str, now: str) -> bool:
        """Return whether stored detail is fresh at the supplied instant."""
        row = self._conn.execute(
            "SELECT detail_fresh_until FROM listings WHERE internal_id = ?", (internal_id,)
        ).fetchone()
        return (
            row is not None
            and row["detail_fresh_until"] is not None
            and str(row["detail_fresh_until"]) > now
        )

    def get_hydration_job(self, athome_key: str) -> HydrationJob | None:
        """Return one durable job by its AtHome listing ID."""
        row = self._conn.execute(
            "SELECT * FROM hydration_jobs WHERE athome_key = ?", (athome_key,)
        ).fetchone()
        return _job_from_row(row) if row is not None else None

    def enqueue_hydration(
        self, intent: HydrationIntent, max_attempts: int = 3
    ) -> HydrationJob | None:
        """Insert one FIFO job unless its detail is already fresh."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = _now()
        existing = self.get_hydration_job(intent.athome_key)
        if self._fresh_detail(intent.internal_id, now):
            if existing is None:
                return None
            if existing.status in {HydrationStatus.QUEUED, HydrationStatus.LEASED}:
                self._conn.execute(
                    "UPDATE hydration_jobs SET status = 'skipped', lease_token = NULL, "
                    "lease_until = NULL, updated_at = ? WHERE athome_key = ?",
                    (now, intent.athome_key),
                )
                self._conn.commit()
            return self.get_hydration_job(intent.athome_key)
        if existing is not None:
            return existing
        self._conn.execute(
            "INSERT INTO hydration_jobs (athome_key, url, internal_id, status, attempts, "
            "max_attempts, created_at, updated_at) VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)",
            (intent.athome_key, intent.url, intent.internal_id, max_attempts, now, now),
        )
        self._conn.commit()
        return self.get_hydration_job(intent.athome_key)

    def claim_hydration(self, lease_seconds: int = 300) -> HydrationJob | None:
        """Atomically claim the oldest queued or expired leased job."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        conn = self._conn
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM hydration_jobs WHERE status = 'queued' OR "
                "(status = 'leased' AND lease_until <= ?) ORDER BY created_at, id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            if self._fresh_detail(str(row["internal_id"]), now):
                conn.execute(
                    "UPDATE hydration_jobs SET status = 'skipped', lease_token = NULL, "
                    "lease_until = NULL, updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                conn.commit()
                return None
            token = str(uuid.uuid4())
            lease_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
            conn.execute(
                "UPDATE hydration_jobs SET status = 'leased', attempts = attempts + 1, "
                "lease_token = ?, lease_until = ?, updated_at = ? WHERE id = ?",
                (token, lease_until, now, row["id"]),
            )
            conn.commit()
            return self.get_hydration_job(str(row["athome_key"]))
        except Exception:
            conn.rollback()
            raise

    def _transition_claimed(
        self, athome_key: str, token: str, status: HydrationStatus, **values: object
    ) -> HydrationJob:
        """Apply a guarded transition to a job held by the supplied lease."""
        conn = self._conn
        row = conn.execute(
            "SELECT id FROM hydration_jobs WHERE athome_key = ? AND status = 'leased' "
            "AND lease_token = ?",
            (athome_key, token),
        ).fetchone()
        if row is None:
            raise ValueError("hydration lease is missing or expired")
        assignments = ["status = ?", "updated_at = ?", "lease_token = NULL", "lease_until = NULL"]
        params: list[object] = [status.value, _now()]
        for name, value in values.items():
            assignments.append(f"{name} = ?")
            params.append(value)
        params.append(row["id"])
        conn.execute(f"UPDATE hydration_jobs SET {', '.join(assignments)} WHERE id = ?", params)
        conn.commit()
        result = self.get_hydration_job(athome_key)
        assert result is not None
        return result

    def acknowledge_hydration_success(self, athome_key: str, lease_token: str) -> HydrationJob:
        """Acknowledge a successfully hydrated listing."""
        return self._transition_claimed(athome_key, lease_token, HydrationStatus.SUCCEEDED)

    def record_hydration_failure(
        self, athome_key: str, lease_token: str, category: str, error: str
    ) -> HydrationJob:
        """Record a categorized retryable or terminal failure."""
        current = self.get_hydration_job(athome_key)
        if (
            current is None
            or current.lease_token != lease_token
            or current.status is not HydrationStatus.LEASED
        ):
            raise ValueError("hydration lease is missing or expired")
        status = (
            HydrationStatus.QUEUED
            if current.attempts < current.max_attempts
            else HydrationStatus.FAILED
        )
        return self._transition_claimed(
            athome_key, lease_token, status, last_error_category=category, last_error=error
        )

    def cancel_hydration(self, athome_key: str, reason: str) -> HydrationJob:
        """Explicitly cancel a job for a positively unavailable listing."""
        conn = self._conn
        conn.execute(
            "UPDATE hydration_jobs SET status = 'cancelled', last_error_category = 'unavailable', "
            "last_error = ?, lease_token = NULL, lease_until = NULL, updated_at = ? "
            "WHERE athome_key = ?",
            (reason, _now(), athome_key),
        )
        conn.commit()
        result = self.get_hydration_job(athome_key)
        if result is None:
            raise KeyError(athome_key)
        return result

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

    def upsert_agency(self, agency: Agency) -> str:
        """Insert or merge an agency keyed by its AtHome member number."""
        conn = self._conn
        now = _now()
        current = self.get_agency(agency.kaiin_no)
        merged = _merge_agency(current, agency)
        conn.execute(
            "INSERT INTO agencies (kaiin_no, payload, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kaiin_no) DO UPDATE SET payload = excluded.payload, "
            "updated_at = excluded.updated_at",
            (merged.kaiin_no, merged.model_dump_json(), now, now),
        )
        conn.commit()
        return merged.kaiin_no

    def get_agency(self, kaiin_no: str) -> Agency | None:
        """Return an agency by AtHome member number, or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT payload FROM agencies WHERE kaiin_no = ?", (kaiin_no,)
        ).fetchone()
        if row is None:
            return None
        return Agency.model_validate_json(str(row["payload"]))

    def link_listing_agency(self, internal_id: str, kaiin_no: str | None) -> None:
        """Link a listing to an agency or clear its agency relationship."""
        conn = self._conn
        row = conn.execute(
            "SELECT payload FROM listings WHERE internal_id = ?", (internal_id,)
        ).fetchone()
        if row is None:
            return
        listing = _deserialize_listing(str(row["payload"]))
        conn.execute(
            "UPDATE listings SET payload = ?, agency_kaiin_no = ?, updated_at = ? "
            "WHERE internal_id = ?",
            (
                listing.model_copy(
                    update={"agency": self.get_agency(kaiin_no) if kaiin_no else None}
                ).model_dump_json(),
                kaiin_no,
                _now(),
                internal_id,
            ),
        )
        conn.commit()

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
