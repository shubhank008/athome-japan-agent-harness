# Marker Contract: Lean Production Cookie Fetcher

The test suite greps for these verbatim. Positive markers MUST appear on a
successful farm; negative markers MUST NOT appear from the production adapter.

## Positive markers (kept)

- `[PATCHRIGHT_FARM_START]`
- `[PLAYWRIGHT_RENDERED]`
- `[PLAYWRIGHT_CHALLENGE]` (only when a challenge is served)
- `[PLAYWRIGHT_VERIFY]` / `[PLAYWRIGHT_VERIFY_RESULT]` (only when a challenge is served)
- `[PLAYWRIGHT_HANDOFF_SAVED]`
- `[PLAYWRIGHT_SESSION_STATE_SAVED]`
- `[PLAYWRIGHT_HANDOFF_REJECTED]` (on rejection)

## Negative markers (must NOT appear from the production adapter)

These belong only to the probe DEBUG mode. After this change the fetcher never
emits them:

- `[PATCHRIGHT_TRACE_STOP_FAILED]`
- `[PATCHRIGHT_VIDEO_FINALIZE_FAILED]`
- `[PLAYWRIGHT_DIAGNOSTICS_SAVED]`
- `[PLAYWRIGHT_DIAGNOSTIC_EVENT]`

## Negative artifact assertions (filesystem)

A default `farm()` must not create any of:

- `playwright_challenge_trace.zip`
- `playwright_challenge.webm`
- `playwright_events.jsonl`
- `playwright_before.html` / `playwright_after.html`
- `playwright_before.png` / `playwright_after.png`

And the persistent-context options must not request `record_video_dir`.
