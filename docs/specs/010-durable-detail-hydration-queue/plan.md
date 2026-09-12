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
