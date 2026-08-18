# Spec: Patchright Browser Runtime

Replace the scraper's Playwright runtime with Patchright while preserving the existing browser cookie handoff, challenge diagnostics, and operator probe behavior. Farmer sessions use a persistent temporary profile with the installed Chrome channel, retain the existing Playwright-Stealth compatibility hook, and do not inject a custom user agent or browser headers.

## Context

The current browser integration imports Playwright and applies `playwright-stealth` fingerprint injection. Patchright provides the same async API, so the integration should use Patchright directly while retaining the existing stealth compatibility hook.

## User Stories

### US-001: Farm a browser handoff with Patchright
**Description:** As a scraper operator, I want the cookie farmer to use Patchright's compatible async API so that browser sessions use the installed Chrome channel with the existing Playwright-Stealth compatibility hook.

**Acceptance Criteria:**
- [x] Production browser imports come from `patchright.async_api`.
- [x] The farmer launches a persistent temporary context with `channel="chrome"`, `headless=True`, and `no_viewport=True`.
- [x] Proxy routing remains bound to the browser context and resulting curl-cffi handoff.
- [x] No custom user agent or browser headers are injected at launch.

### US-002: Preserve diagnostics and probe behavior
**Description:** As an operator, I want existing challenge evidence and manual observation behavior to remain available after the runtime switch.

**Acceptance Criteria:**
- [x] Challenge HTML, screenshots, trace, video, and redacted events retain their existing names and lifecycle.
- [x] The manual probe uses Patchright and the same Chrome configuration.
- [x] Unit tests continue to exercise the public farmer behavior without requiring a live browser.

## Functional Requirements

- FR-1: Replace runtime imports of `playwright.async_api` with `patchright.async_api`.
- FR-2: Replace the production browser dependency pin with an exact Patchright pin compatible with the installed runtime.
- FR-3: Retain the Playwright-Stealth dependency and apply its compatibility hook to Patchright pages.
- FR-4: Use a temporary persistent user-data directory and close the context before removing it.
- FR-5: Preserve fail-closed challenge handling and never automate puzzle solving or slider dragging.
- FR-6: Keep debug artifacts local and excluded from version control.

## Non-Goals

- Automating Geetest or other anti-bot puzzle completion.
- Adding custom user-agent strings or browser-header overrides.
- Renaming the public `PlaywrightCookieFetcher` class or existing diagnostic filenames.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Existing render wait | 3s | USER / prior feature contract |
| Existing click hold | 2.5s | USER / prior feature contract |
| Browser viewport | no viewport | USER-provided Patchright best practice |
| Farmer browser mode | headless | Existing cookie-farmer behavior |
| Manual probe browser mode | headed | USER-provided diagnostic workflow |

## Success Metrics

- Unit tests, Ruff, and mypy pass with the Patchright imports and lifecycle.
- A live authorized session can start the headless Patchright Chrome context or fail with a clear browser-environment error.
- Existing diagnostic artifact contracts remain intact.

## Open Questions

- Whether the execution host has a discoverable system Chrome and display server remains environment-specific. Owner: operator.
