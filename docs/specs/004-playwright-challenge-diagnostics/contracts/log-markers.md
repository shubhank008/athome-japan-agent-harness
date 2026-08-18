# Diagnostic Marker Contract

## Required markers

| Marker | Meaning |
|---|---|
| `[PLAYWRIGHT_DIAGNOSTIC_EVENT]` | A redacted structured event was written. |
| `[PLAYWRIGHT_VERIFY_TARGET]` | The selected control and frame metadata were recorded. |
| `[PLAYWRIGHT_VERIFY_POINTER]` | The bounded pointer interaction began or ended. |
| `[PLAYWRIGHT_VERIFY_RESULT]` | Post-interaction challenge validation completed. |
| `[PLAYWRIGHT_DIAGNOSTICS_SAVED]` | Video, trace, HTML, screenshots, and event log paths were finalized. |

## Required fields

`[PLAYWRIGHT_DIAGNOSTIC_EVENT]` JSON payloads always contain `event` and `timestamp`.
Event-specific fields include: `challenge_kind`, `html_chars`, `url` (redacted),
`attempted`, `accepted`, `frame_index`, `frame_url`, `target_kind`, `visible`,
`bounding_box`, `action`, `duration_ms`, `error_type`, `artifacts`, `mode`,
`found`, `body_sha256`, `event` (name), and `kind`. They must not contain cookie
values, proxy credentials, full query-bearing URLs, or arbitrary page text.

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
