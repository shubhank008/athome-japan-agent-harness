# T32 / G4 implementation plan

## Files

- `models.py`: typed queue status and job contracts.
- `store/base.py`: durable queue interface.
- `store/sqlite_store.py`: schema v3, migration, FIFO claim/lease transactions, and transitions.
- `store/__init__.py`: public queue exports.
- `tests/unit/test_sqlite_store.py`: real SQLite queue and migration regressions.
- Relevant reference docs: data models, store, and server app state.

## Global constraints

Preserve existing store methods and migrations. Do not add dependencies, worker execution, network calls, priority scheduling, cache integration, or root `PLAN.md`/`AGENTS.md` edits. Keep challenge, block, timeout, and parser failures retryable and categorized.

## T33 / G5 worker extension

- `scraping/detail_hydration_worker.py` consumes one FIFO claim at a time and injects
  fetch, parser, clock, sleeper, and rate-limiter boundaries.
- `scripts/detail_hydration_worker.py` is disabled by default; `--loop` enables bounded
  polling using explicit worker settings.
- Success persists complete detail and agency, then applies the 14-day freshness policy
  before acknowledgement. Challenge/block stops the run; only documented unavailable
  markers cancel a job.
- Priority scheduling remains out of scope for T34.

## T34 / G6 live LLM pipeline cache integration

- `BaseDataStore.get_fresh_detail` and `SqliteStore.get_fresh_detail` read only complete,
  unexpired detail records by canonical AtHome listing ID using timezone-aware timestamps.
- `SearchSession` checks that read before each detail request. A miss directly fetches and
  parses detail, persists structured detail and agency data with a fourteen-day freshness
  window, and immediately uses the hydrated result.
- The live stage never enqueues, leases, claims, or waits for background hydration work.
