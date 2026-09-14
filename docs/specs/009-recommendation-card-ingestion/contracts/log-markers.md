# T31 marker contract

T31's ingestion is a library path and does not emit runtime markers. Its evidence is
provided by fixture-backed tests covering card count, identity, normalization, lifecycle
preservation, idempotence, and optional-field tolerance.

Forbidden scope markers:

- No queue-created marker.
- No worker-started marker.
- No live-cache marker.
