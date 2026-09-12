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
| `upsert_agency` | `(agency: Agency) -> str` | Insert or update an agency keyed by `kaiin_no`. |
| `get_agency` | `(kaiin_no: str) -> Agency \| None` | Fetch an agency by AtHome member number. |
| `link_listing_agency` | `(internal_id: str, kaiin_no: str \| None) -> None` | Set or clear a listing's agency relationship. |
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


## Detail hydration worker boundary

`DetailHydrationWorker` consumes the queue methods on `BaseDataStore` in strict FIFO
order: claim, fetch through an injected production scraper, challenge/availability
validation, detail parsing, `upsert_listing` plus agency persistence, and success
acknowledgement. It processes one claim at a time, uses the existing rate limiter,
and applies a 14-day detail freshness interval (`DESIGN-FRESH`). Timeout, parser,
identity, challenge, and block failures are categorized through the queue failure
transition. Challenge and block stop the current run; only explicit unavailable
markers can cancel a job. The command is disabled unless
`ATHOME_HYDRATION_WORKER_ENABLED=true`; T34 live cache integration is not included.

`store/base.py` also ships `StoreContractSuite`, a pytest-ready mixin that
exercises every contract method against any implementation, so a new backend
inherits the full contract test surface for free.

## SqliteStore

`SqliteStore(path: str | Path)` implements the contract over a single SQLite
database file (default `athome.db`, configurable via `ATHOME_STORE_PATH`).

* **Schema versioning:** `SCHEMA_VERSION = 2`; `migrate(connection)` creates or
  upgrades the schema idempotently and `_read_version` reads the current
  version, so opening an older file upgrades in place.
* **Listing storage:** listings are serialized to JSON and upserted keyed by
  `internal_id`, with the AtHome `BKLISTID` and URL stored alongside for dedupe.
  Rich detail fields are merged non-destructively, and agencies are stored
  separately by `kaiin_no` and linked through the listing row.
* **Feedback:** one feedback row per listing (`save` or `reject`); the last
  action wins and `clear_feedback` removes it.
* **Cache meta:** a small key-value table reserved for post-MVP cache bookkeeping;
  its former `ATHOME_PREFETCH_TTL_HOURS` use is superseded by US-009 detail freshness.
* **Connection lifecycle:** the connection is opened lazily per-thread and
  `close()` releases it. Always call `close()` when the store is no longer
  needed; the probes and the CLI do this in `finally` blocks.

## Planned detail-hydration extension

US-009 will extend this contract without making the live LLM path depend on a worker.
The planned SQLite migration adds an `Agency` entity keyed by AtHome `kaiinNo`, listing
completeness (`summary_partial`, `summary_complete`, `detail_complete`), 14-day detail
freshness metadata, and a durable FIFO detail-job queue. `otherPropertyData` cards will
be normalized as `summary_complete`; full detail parses will upsert and link an agency
record without duplicating it into each listing.

The queue is a background optimization only. A live request uses a fresh
`detail_complete` record, or directly fetches and upserts current detail when it is
missing or stale. The worker independently claims, rechecks, fetches, validates, parses,
upserts, and acknowledges jobs. A verified unavailable page deletes the listing and
related records; challenge/block/timeout/parser failure is retryable and must not delete
data.


## Session memory semantics

The funnel uses the store as its memory boundary in two places:

1. **Before the harvest** is not filtered by the store: the harvest pages are
   fetched and parsed fresh every run.
2. **After the harvest**, two queries shape the shortlist: rejected listings
   are excluded from shortlist candidates, and `seen_internal_ids` is available
   for dedupe-aware flows. Recommendations are recorded under the search ID so
   `more_like`/`refine` feedback commands can reason about past runs.
