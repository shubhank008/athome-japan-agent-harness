# T32 marker contract

The queue is a persistence boundary and emits no runtime worker markers yet.

## Required implementation evidence

- `HYDRATION_QUEUE_ENQUEUED`: enqueue inserted or found the durable job.
- `HYDRATION_QUEUE_CLAIMED`: one job was atomically leased.
- `HYDRATION_QUEUE_SKIPPED_FRESH`: claim-time freshness suppressed work.
- `HYDRATION_QUEUE_RETRYABLE_FAILURE`: failure was categorized and requeued.
- `HYDRATION_QUEUE_CANCELLED`: explicit unavailable-listing cancellation completed.

## Forbidden patterns

- `HYDRATION_QUEUE_PRIORITY`
- `HYDRATION_WORKER_STARTED`
- network fetch or parser execution from queue methods
