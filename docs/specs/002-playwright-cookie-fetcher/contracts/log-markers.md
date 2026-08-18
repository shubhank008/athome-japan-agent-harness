# Marker Contract 002: Playwright Cookie Fetcher

The farmer emits one marker per line. Values are diagnostic only and must never
contain cookie values, proxy credentials, client IP addresses, or full URLs with
query strings.

## Happy-path markers

```
[PLAYWRIGHT_FARM_START] url=<redacted> proxy=<direct|sanitized-host>
[PLAYWRIGHT_RENDERED] html_chars=<n> blocked=<true|false>
[PLAYWRIGHT_HANDOFF_SAVED] proxy=<direct|sanitized-host> cookies=<n>
```

## Challenge markers

```
[PLAYWRIGHT_CHALLENGE] kind=<puzzle|javascript>
[PLAYWRIGHT_VERIFY] clicked=<true|false>
[PLAYWRIGHT_HANDOFF_REJECTED] reason=<challenge|render>
```

## Failure patterns

These must not appear in logs, exception messages, or marker output:

```
PROXY_CREDENTIALS_IN_URL_LOG
COOKIE_VALUE_IN_LOG
PLAYWRIGHT_CHALLENGE_SOLVED_BY_DRAG
```

## Rules

- URLs use the existing `redact_url` helper and therefore contain no query string or
  userinfo.
- The `proxy` field is a sanitized host/port identity only.
- `[PLAYWRIGHT_RENDERED]` is emitted after each render validation, including after a
  verification click.
- `[PLAYWRIGHT_HANDOFF_SAVED]` appears only after JSON and cookie-header persistence
  succeeds.
