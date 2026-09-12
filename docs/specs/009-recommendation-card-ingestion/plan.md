# T31 / G3 implementation plan

## Files

- `models.py`: typed card, provenance, and hydration-intent contracts.
- `scraping/server_app_state.py`: validated candidate extraction separate from detail.
- `scraping/recommendation_cards.py`: normalization and BaseDataStore ingestion.
- `store/sqlite_store.py`: monotonic lifecycle and sparse-field merge protection.
- `tests/unit/test_recommendation_cards.py`: representative fixture regressions.
- `docs/reference/data-models.md` and `server-app-state.md`: public contract updates.

## Constraints

No new dependencies, queue table, worker, live cache integration, or root planning-file
changes. Preserve `detail_complete` records and existing agency data.
