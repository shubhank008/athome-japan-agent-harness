# Spec: Structured Detail and Agency Persistence

Persist useful full-detail AtHome server state while keeping agency data separate from the listing and preserving the existing LLM projection.

## Context

T30 / G2 extends the accepted T29 listing lifecycle and agency contract. The curated server state contains rich operational, facility, cost, transit, image, and nearby-facility data that should survive detail hydration.

## User Stories

### US-001: Inspect rich detail
**Description:** As a user, I want a hydrated listing to retain structured detail so that later workflows can use data beyond the LLM summary.

**Acceptance Criteria:**
- [ ] Server state exposes typed rich detail and agency data, including Romanized values and raw values where available.
- [ ] Recommendation-card data in `otherPropertyData` is not converted into listings.

### US-002: Reuse agency records
**Description:** As a user, I want agencies stored independently and linked by `kaiinNo` so that repeated listings share one agency record.

**Acceptance Criteria:**
- [ ] Agency records upsert by `kaiinNo` and incomplete updates preserve existing non-null fields.
- [ ] Listing detail upserts preserve richer fields when later input omits them.

## Functional Requirements

- FR-1: Parse agency `kaiinInfo`, surrounding map/facility data, access, images, feature categories, costs, appeal, building, and other-property information.
- FR-2: Persist rich values through `BaseDataStore` and `SqliteStore` without changing LLM projection behavior.
- FR-3: Maintain compatibility with existing summary/detail parsing and old SQLite databases.
- FR-4: Link each detail listing to its agency using `kaiinNo`.

## Non-Goals

- Recommendation-card ingestion from `otherPropertyData`.
- Queue, worker, or live cache integration.
- Deletion of unavailable listings.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Required curated image records | 39 | VERIFIED from `data/server-app-state-1106831830.full.json` |
| Required nearby facility records | 8 | VERIFIED from `data/server-app-state-1106831830.full.json` |

## Success Metrics

Fixture-backed tests demonstrate extraction, round-trip persistence, linkage, Romanized fields, and non-destructive updates.

## Open Questions

- None for T30. Future recommendation ingestion owns `otherPropertyData`.
