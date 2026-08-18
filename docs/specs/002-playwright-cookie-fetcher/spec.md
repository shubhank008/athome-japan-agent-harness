# Spec: Playwright Cookie Fetcher

A blocked AtHome HTTP session can briefly switch to a real browser to obtain a
fresh, proxy-bound session handoff. The browser visits the broad rental search
page, renders JavaScript, optionally lets the user-approved AtHome verification
link run, and stores the resulting cookies, exact user agent, request headers,
and proxy identity locally so fast curl-cffi workers can reuse that session.

## Context

AtHome can return an HTTP 200 Incapsula/WAF challenge instead of listing HTML.
The existing HTTP adapter detects the challenge but cannot render the JavaScript
needed to obtain a valid browser session. This feature adds a bounded Patchright
farmer without moving browser dependencies into business logic or attempting to
automate puzzle-piece dragging.

## User Stories

### US-001: Farm a browser session
**Description:** As a scraper worker, I want a Patchright browser to render the
AtHome broad search page so that I can hand a valid browser session to curl-cffi.

**Acceptance Criteria:**
- [x] The farmer uses a persistent Patchright Chrome context with the stealth
      compatibility hook, supports direct or explicitly configured proxy routing,
      and visits the configured AtHome broad search URL.
- [x] The farmer waits 3 seconds after navigation before validating the rendered page.
- [x] A successful handoff contains non-empty rendered HTML, cookies, the exact
      `navigator.userAgent`, request headers, and the proxy identity used.
- [x] The handoff exposes curl-cffi-compatible headers, cookies, proxy, and browser
      impersonation settings without exposing proxy credentials in logs.

### US-002: Handle an AtHome challenge transparently
**Description:** As an operator, I want challenge evidence captured before and after
verification so that failed WAF behavior can be diagnosed without guessing.

**Acceptance Criteria:**
- [x] Existing AtHome challenge markers classify a rendered page as blocked before
      it is accepted as a handoff.
- [x] On a blocked page, the farmer writes `debug/playwright_before.html`,
      `debug/playwright_before.png`, `debug/playwright_after.html`, and
      `debug/playwright_after.png`.
- [x] The farmer clicks only the visible `Click to verify` control when present,
      waits 3 seconds, and validates the resulting page again. It never drags a
      puzzle piece or attempts to defeat the challenge algorithm.
- [x] If the challenge remains, the farmer raises a typed error and does not save
      the challenged page as a usable handoff.

### US-003: Persist and reload a handoff
**Description:** As a worker process, I want to load a handoff from local JSON so
that many curl-cffi requests can reuse the same browser session until it expires.

**Acceptance Criteria:**
- [x] The farmer writes a JSON handoff tied to a sanitized proxy identity and writes
      the full cookie header to `debug/cookies.txt` for local diagnostics.
- [x] JSON reload validates the schema and preserves cookie values, headers, user
      agent, and proxy identity.
- [x] Debug artifacts and cookie values remain ignored by version control.

## Functional Requirements

- FR-1: Browser lifecycle must be async, single-instance per farm, and closed in a
  `finally` path after the handoff is captured.
- FR-2: Patchright and stealth imports must remain inside the concrete adapter module;
  business-facing code consumes the typed handoff only.
- FR-3: Challenge detection must reuse the existing AtHome marker semantics and must
  reject empty or undersized rendered documents before persistence.
- FR-4: Proxy credentials must never appear in marker lines, filenames, or exception
  messages. The handoff stores only the sanitized proxy identity, not the credentialed URL.
- FR-5: curl-cffi workers must use the harvested proxy, captured user agent, captured
  request headers, and cookies together; callers must not silently substitute a different
  proxy or user agent.
- FR-6: A failed or still-blocked browser session must not overwrite the last successful
  handoff.

## Non-Goals

- No puzzle-piece dragging, CAPTCHA solving, or WAF cryptanalysis.
- No persistent browser process between farms.
- No automatic rotation policy; callers provide the proxy selected by their existing
  proxy provider.
- No storage of browser profiles, passwords, or arbitrary local browsing history.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Post-navigation validation wait | 3s | USER |
| Post-verification validation wait | 3s | USER |
| Minimum rendered HTML length | 200 characters | DESIGN-FRESH |
| Handoff schema version | 1 | DESIGN-FRESH |

## Success Metrics

- Unit tests prove success, challenge retry, persistent challenge, invalid render,
  proxy identity redaction, JSON persistence, and curl-cffi kwargs.
- A browser-independent end-to-end-style test exercises the complete farmer state
  machine and emits every happy-path marker without network access.
- `ruff`, `mypy`, and `pytest` pass, and no challenge HTML is returned as a handoff.

## Open Questions

- The exact lifetime of an AtHome clearance token remains an operational concern;
  callers should refarm after the HTTP adapter reports a fresh block. Owner: scraper
  integration milestone.
