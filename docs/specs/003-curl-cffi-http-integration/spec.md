# Spec: curl-cffi HTTP Scraper Integration

The main AtHome HTTP scraper uses curl-cffi browser impersonation for every request and can consume a short-lived Playwright browser handoff. A scraper operator can farm a session once, use the exact proxy, user agent, headers, and cookies for several detail-page requests, and receive a bounded block signal when the clearance expires so the caller can farm a replacement.

## Context

The existing `HttpDomAdapter` uses httpx, while the project already supplies `curl-cffi` and a Playwright cookie handoff. This leaves the primary scraper unable to use the intended browser TLS fingerprint or browser-farmed clearance cookies. The integration must preserve the `BaseScraper`, block detection, proxy rotation, parser, and redacted logging contracts.

## User Stories

### US-001: Use curl-cffi browser impersonation
**Description:** As a scraper worker, I want the main HTTP adapter to use curl-cffi with a browser profile so AtHome receives a browser-like TLS and HTTP fingerprint.

**Acceptance Criteria:**
- [ ] `HttpDomAdapter` uses curl-cffi for HTML and binary GET requests, never httpx.
- [ ] Requests use the `chrome` curl-cffi impersonation profile by default.
- [ ] The configured HTTP timeout is passed to every curl-cffi request.
- [ ] Existing block detection, retry, DOM parsing, proxy rotation, and close behavior remain intact.

### US-002: Reuse a Playwright browser handoff
**Description:** As a scraper worker, I want to pass a `CookieHandoff` to the HTTP adapter so detail requests reuse the exact browser session that farmed the clearance.

**Acceptance Criteria:**
- [ ] The adapter accepts an optional `CookieHandoff` and passes its exact user agent, request headers, cookies, proxy, and impersonation profile to curl-cffi.
- [ ] A handoff-bound request never silently switches to another proxy after a block; it raises `BlockDetected` so the caller can refarm.
- [ ] Without a handoff, existing direct-first proxy-provider rotation remains available.
- [ ] No cookie values or proxy credentials appear in logs or exception messages.

### US-003: Verify end-to-end handoff use
**Description:** As an operator, I want a test that farms a browser handoff and uses the curl adapter to fetch multiple AtHome detail pages with a short timeout, so the integration is demonstrably wired together.

**Acceptance Criteria:**
- [ ] A live-marked integration test uses `PlaywrightCookieFetcher`, then the main `HttpDomAdapter`, and fetches 3 to 5 configured detail URLs with a 2-second request timeout.
- [ ] Each successful response is parsed through the existing detail parser and is not an AtHome challenge page.
- [ ] The live test is skipped by default and reports a clear setup/network failure rather than saving challenged HTML as a fixture.
- [ ] Offline unit tests verify exact handoff request kwargs and the expired-handoff block behavior.

## Functional Requirements

- FR-1: Third-party HTTP imports remain confined to `http_adapter.py`; business-facing code continues to depend on `BaseScraper` and `CookieHandoff`.
- FR-2: The adapter's default curl-cffi profile is `chrome`; a handoff may provide the profile used during farming.
- FR-3: Handoff cookies, headers, user agent, and proxy are passed together on each request.
- FR-4: A block on a handoff-bound session raises `BlockDetected` without rotating the handoff to a different IP.
- FR-5: Existing non-handoff proxy rotation and transient retry semantics remain bounded and redacted.
- FR-6: The integration test uses only existing captured detail URLs or explicitly configured live URLs and never writes network responses to fixtures.

## Non-Goals

- No automatic asynchronous refarming inside the synchronous HTTP adapter.
- No puzzle dragging, CAPTCHA solving, or WAF cryptanalysis.
- No persistent browser process or browser profile.
- No change to parser behavior or property model schemas.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Default curl request timeout | Existing `Budgets.http_timeout_s` default 30s | VERIFIED in `config.py` |
| Live integration request timeout | 2s | USER |
| Live detail pages | 3 pages | USER requested 3-5; DESIGN-FRESH lower bound |
| curl-cffi impersonation profile | `chrome` | DESIGN-FRESH, compatible with Chromium farmer |

## Success Metrics

- Unit coverage proves curl-cffi request construction, handoff binding, block behavior, retries, and DOM parsing.
- A live-marked test farms one browser handoff and parses three detail pages through curl-cffi when network, Chromium, and AtHome access are available.
- `pytest`, `ruff`, `mypy`, and whitespace validation pass without changing the abstract scraper contract.

## Open Questions

- Whether AtHome accepts the farmed token for the full detail batch remains an operational live-test result. Owner: scraper integration milestone.
