# T31 / G3: Recommendation-card normalization and ingestion

## Goal

Normalize validated `first-view-ITEMS.propertyData.otherPropertyData[]` cards into
`ListingSummary` records and persist them as `summary_complete` candidates.

## Contract

- The target `rentInfo` remains the detail record. `otherPropertyData` is exposed as
  a separate candidate-card result and is never parsed as full detail.
- AtHome card `id` is the stable dedupe identity. URLs use `seoRoma` and `id`.
- Card values populate the existing summary fields, retain raw source provenance, and
  tolerate absent optional nested fields.
- Ingestion is idempotent, does not downgrade `detail_complete`, and does not replace
  useful detail or agency values with sparse card values.
- Hydration intents are returned as in-memory metadata only. Durable queue persistence
  and workers are T32 and T33, respectively.

## Non-goals

T31 does not implement a queue table, queue worker, cache integration, or live scraping.
