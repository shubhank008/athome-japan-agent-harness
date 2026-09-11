# Log marker contract: OpenCodeGo session headers

No new runtime log markers are emitted. The session ID and credentials are request metadata and must not appear in logs.

## Required evidence

- Focused tests assert `User-Agent` and `x-opencode-session` on OpenCodeGo requests.
- Focused tests assert one provider instance reuses one session ID.
- A live probe progresses past the prior `MissingSessionID` response.

## Forbidden patterns

- `x-opencode-session` values in logs, reports, fixtures, or exception messages.
- Authorization credentials in logs, reports, fixtures, or exception messages.
