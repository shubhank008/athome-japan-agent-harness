# Marker Contract 003: curl-cffi HTTP Scraper Integration

The adapter emits marker lines with redacted URLs and non-sensitive identities only. No marker may contain cookie values, proxy credentials, or full query strings.

## Happy-path markers

```
[CURL_REQUEST] url=<redacted> impersonate=<chrome> timeout=<seconds>
[CURL_HANDOFF_BOUND] proxy=<direct|sanitized-host> cookies=<n> impersonate=<chrome>
[CURL_PLAYWRIGHT_INTEGRATION] pages=<3> timeout=<2>
```

## Failure and recovery markers

```
[CURL_BLOCK_REHANDOFF] url=<redacted> signature=<403|429|captcha>
[BLOCK_DETECTED] url=<redacted> signature=<403|429|captcha>
[PROXY_ROTATE] attempt=<n> of=<n>
[PROXY_RECOVERED] via=proxy
```

## Failure patterns

These must not appear in logs, exception messages, or marker output:

```
PROXY_CREDENTIALS_IN_URL_LOG
COOKIE_VALUE_IN_LOG
HTTPX_REQUEST
CURL_HANDOFF_PROXY_ROTATED
```

## Rules

- `HttpDomAdapter` is still the public concrete adapter name for compatibility.
- Every curl-cffi request uses the configured timeout and the `chrome` profile unless a validated handoff supplies its profile.
- A handoff-bound block emits `[CURL_BLOCK_REHANDOFF]` and raises `BlockDetected`; it does not use a different proxy with the old cookie.
- The integration marker appears only after all three detail pages have been fetched and parsed successfully.
