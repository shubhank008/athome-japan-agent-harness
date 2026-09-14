# Plan: Structured Detail and Agency Persistence

1. Add typed rich detail value objects and optional fields to `ListingDetail` and `Agency`.
2. Extend `server_app_state.py` with validated extraction of rich state and agency data while ignoring `otherPropertyData` as listings.
3. Hydrate rich state in `detail_parser.py` without changing the LLM-facing fields used by the recommender.
4. Extend the abstract store contract and SQLite JSON persistence with non-destructive merges and schema compatibility.
5. Add the curated full payload as a fixture only if it is absent from this branch, then test extraction, linkage, round trips, and partial updates.
6. Update `docs/reference/server-app-state.md`, `docs/reference/data-models.md`, and `docs/reference/store.md`.
7. Run focused tests, full pytest, ruff, and mypy; inspect diff and commit.

## Global Constraints

- Do not implement T31 recommendation-card ingestion, T32 queue, T33 worker, or T34 live cache integration.
- Keep agency as a separate entity keyed by `kaiinNo`.
- Preserve richer stored data when incoming optional fields are absent.
- Do not edit `PLAN.md` or `AGENTS.md`; the main orchestrator owns them.
- No new dependencies, secrets, challenge solving, or external publication.
