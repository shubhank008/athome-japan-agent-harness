# Store layer

The persistence building blocks under `src/athome_harness/store/`:
`base.py` (the abstract contract, milestone M5/T22) and `sqlite_store.py` (the
SQLite implementation). The store is the session-memory boundary: listings,
searches, recommendations, and the user's saved/rejected feedback survive across
runs.

* **Depends on:** [data models](data-models.md) (`ListingSummary`,
  `SearchPlan`, `Recommendation`).
* **Depended on by:** the [architecture funnel](architecture.md) (`SearchSession`
  persists every harvested listing and records the search) and the
  [providers factory](providers.md) (`build_store`).

## BaseDataStore

The abstract contract every backend implements. All methods are abstract; the
SQLite implementation is the only one today.

| Method | Signature | Meaning |
|--------|-----------|---------|
| `upsert_listing` | `(listing: ListingSummary) -> str` | Insert or update one listing; returns its internal ID. |
| `get_listing` | `(internal_id: str) -> ListingSummary \| None` | Fetch one listing by ID. |
| `list_listings` | `() -> list[ListingSummary]` | List every stored listing. |
| `record_search` | `(query: str, plan: SearchPlan) -> int` | Record one executed search; returns the backend search ID. |
| `search_history` | `(limit: int = 20) -> list[SearchRecord]` | Most recent searches. |
| `record_recommendation` | `(search_id: int, recommendations: list[Recommendation]) -> None` | Persist the recommendations a search produced. |
| `recommendation_history` | `(limit: int = 50) -> list[RecommendationRecord]` | Most recent recommendation records. |
| `save_listing` | `(internal_id: str) -> None` | Mark a listing saved by the user. |
| `reject_listing` | `(internal_id: str) -> None` | Mark a listing rejected by the user. |
| `is_saved` | `(internal_id: str) -> bool` | Whether the listing is saved. |
| `is_rejected` | `(internal_id: str) -> bool` | Whether the listing is rejected. |
| `saved_internal_ids` | `() -> set[str]` | All saved listing IDs. |
| `rejected_internal_ids` | `() -> set[str]` | All rejected listing IDs. |
| `seen_internal_ids` | `() -> set[str]` | Every listing ID the store has ever upserted. |
| `clear_feedback` | `(internal_id: str) -> None` | Remove saved/rejected feedback for one listing. |
| `set_cache_meta` | `(key: str, value: str \| int \| float) -> None` | Write a cache metadata entry. |
| `get_cache_meta` | `(key: str) -> str \| int \| float \| None` | Read a cache metadata entry. |

`SearchRecord` fields: `search_id: int`, `query: str`, `plan: SearchPlan`,
`created_at: str` (ISO-8601). `RecommendationRecord` fields: `search_id: int`,
`recommendation: Recommendation`, `created_at: str`.

`store/base.py` also ships `StoreContractSuite`, a pytest-ready mixin that
exercises every contract method against any implementation, so a new backend
inherits the full contract test surface for free.

## SqliteStore

`SqliteStore(path: str | Path)` implements the contract over a single SQLite
database file (default `athome.db`, configurable via `ATHOME_STORE_PATH`).

* **Schema versioning:** `SCHEMA_VERSION = 1`; `migrate(connection)` creates or
  upgrades the schema idempotently and `_read_version` reads the current
  version, so opening an older file upgrades in place.
* **Listing storage:** listings are serialized to JSON and upserted keyed by
  `internal_id`, with the AtHome `BKLISTID` and URL stored alongside for
  dedupe.
* **Feedback:** one feedback row per listing (`save` or `reject`); the last
  action wins and `clear_feedback` removes it.
* **Cache meta:** a small key-value table reserved for the post-MVP prefetch
  cache bookkeeping (`ATHOME_PREFETCH_TTL_HOURS` gates that feature, which is
  not scheduled).
* **Connection lifecycle:** the connection is opened lazily per-thread and
  `close()` releases it. Always call `close()` when the store is no longer
  needed; the probes and the CLI do this in `finally` blocks.

## Session memory semantics

The funnel uses the store as its memory boundary in two places:

1. **Before the harvest** is not filtered by the store: the harvest pages are
   fetched and parsed fresh every run.
2. **After the harvest**, two queries shape the shortlist: rejected listings
   are excluded from shortlist candidates, and `seen_internal_ids` is available
   for dedupe-aware flows. Recommendations are recorded under the search ID so
   `more_like`/`refine` feedback commands can reason about past runs.
