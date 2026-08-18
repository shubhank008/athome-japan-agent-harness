# Diagnostic Marker Contract

## Required markers

| Marker | Meaning |
|---|---|
| `[PLAYWRIGHT_DIAGNOSTICS_START]` | Video/trace/event diagnostics were enabled for a farm. |
| `[PLAYWRIGHT_DIAGNOSTIC_EVENT]` | A redacted structured event was written. |
| `[PLAYWRIGHT_VERIFY_TARGET]` | The selected control and frame metadata were recorded. |
| `[PLAYWRIGHT_VERIFY_POINTER]` | The bounded pointer interaction began or ended. |
| `[PLAYWRIGHT_VERIFY_RESULT]` | Post-interaction challenge validation completed. |
| `[PLAYWRIGHT_MANUAL_PROBE]` | The headed manual-observation probe started or ended. |
| `[PLAYWRIGHT_DIAGNOSTICS_SAVED]` | Video, trace, HTML, screenshots, and event log paths were finalized. |

## Required fields

`[PLAYWRIGHT_DIAGNOSTIC_EVENT]` JSON payloads may contain event name, timestamp,
frame URL, frame index, target kind, target count, visibility, bounding box,
HTML length, challenge kind, URL path, title, request method, response status,
and cookie names/counts. They must not contain cookie values, proxy credentials,
full query-bearing URLs, or arbitrary page text.

## Failure patterns

The following must not be emitted by normal diagnostics:

- `[PLAYWRIGHT_VERIFY_RESULT] accepted=true` while `challenge_kind` is non-null.
- Cookie values or credential-bearing proxy URLs.
- Any marker claiming puzzle or CAPTCHA completion.
- Unbounded repeated click or drag attempts.

## Artifact names

- `playwright_before.html`, `playwright_before.png`
- `playwright_after.html`, `playwright_after.png`
- `playwright_challenge.webm`
- `playwright_challenge_trace.zip`
- `playwright_events.jsonl`
