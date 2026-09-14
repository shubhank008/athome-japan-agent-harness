# T32 / G4: Durable detail-hydration queue

## Goal

Persist recommendation-card detail-hydration work as a deduplicated FIFO queue that a future T33 worker can safely consume.

## Contract

- `HydrationJob` contains the AtHome listing ID, canonical URL, internal listing ID, lifecycle status, attempt and retry metadata, creation/update timestamps, lease metadata, and categorized last error information.
- Enqueue is idempotent by AtHome listing ID and returns no new work when the stored listing already has fresh detail.
- Claims are atomic across SQLite processes. One eligible queued job is claimed in `created_at` order. An expired lease is reclaimable.
- A claim rechecks freshness. A fresh listing transitions the job to `skipped` without returning it to a worker.
- Successful acknowledgement transitions a claimed job to `succeeded`. Retryable failures record category/error and requeue until the bounded attempt limit; the terminal failure remains `failed`.
- Cancellation is explicit and transitions a job to `cancelled`; it is intended only for positively verified unavailable listings.

## Non-goals

T32 does not fetch or parse pages, run a worker loop, integrate the live cache, add priority scheduling, or solve challenges/blocks.

## Design freshness

The default maximum attempts (`3`) and default lease duration (`300` seconds) are `DESIGN-FRESH` queue safety defaults.
