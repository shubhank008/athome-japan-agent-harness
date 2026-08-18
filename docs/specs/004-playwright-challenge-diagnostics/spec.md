# Spec: Playwright Challenge Diagnostics

An operator can inspect exactly what the Playwright farmer sees and does around an AtHome verification challenge, including a short browser video, trace, screenshots, raw HTML, and redacted event log. An opt-in headed probe lets the operator manually observe and interact with the page from a local browser session, while the scraper never automates puzzle solving or WAF bypass behavior.

## Context

The existing farmer records before/after HTML and screenshots, but its `clicked=true` marker only means that Playwright accepted a click command. It does not identify the selected frame/control, show the pointer action, or prove that the challenge accepted the interaction. AtHome's captured challenge page may require a puzzle interaction after the initial verification control. Better evidence is needed before changing behavior further.

## User Stories

### US-001: Diagnose a verification attempt
**Description:** As an operator, I want a video, trace, HTML, screenshots, and structured redacted log around a verification attempt so that I can distinguish a missed click from a challenge that remains after a real click.

**Acceptance Criteria:**
- [x] The farmer records a WebM video for a challenge session when diagnostics are enabled.
- [x] The farmer records a Playwright trace with screenshots and DOM snapshots when diagnostics are enabled.
- [x] The farmer records pre-click and post-click HTML/screenshots and a JSON-lines event log without cookie values or proxy credentials.
- [x] The event log identifies the selected frame, target kind, visibility, bounding box, click timing, and post-click challenge result.

### US-002: Use a robust, bounded verification interaction
**Description:** As an operator, I want the farmer to locate a visible verification control in the page or its frames and perform one bounded click attempt so that the interaction itself is observable without automating a WAF puzzle.

**Acceptance Criteria:**
- [x] The farmer searches the main page and child frames and waits for the selected control to become visible.
- [x] The farmer prefers semantic button/link controls and only falls back to matching visible text.
- [x] The farmer performs at most one click attempt and validates the page after the configured wait.
- [x] A click attempt is not reported as challenge success unless challenge markers disappear.
- [x] The farmer never drags puzzle pieces, solves CAPTCHA challenges, or attempts WAF cryptanalysis.

### US-003: Manually inspect a headed browser session
**Description:** As an operator, I want an opt-in headed probe that pauses for my manual inspection and interaction so that I can report what the real browser displays from my own network.

**Acceptance Criteria:**
- [x] A documented command launches a headed Playwright session against the configured URL.
- [x] The probe records the same safe diagnostics before and after the manual observation window.
- [x] The probe does not automate slider movement or submit a solved challenge.
- [x] The probe explains that it must run on the operator's machine to use the operator's public IP and browser window.

## Functional Requirements

- FR-1: Diagnostic artifacts must remain local under the configured debug directory and must be ignored by version control.
- FR-2: Logs may include cookie names and counts, but never cookie values, proxy credentials, or full query-bearing URLs.
- FR-3: Browser lifecycle must close video and trace artifacts in `finally` paths.
- FR-4: Existing challenge markers remain the source of truth for post-interaction acceptance.
- FR-5: The manual probe must be opt-in and must not run during normal headless farming.
- FR-6: Diagnostics must be bounded by the configured wait and one verification attempt.

## Non-Goals

- Automated dragging of AtHome puzzle pieces or any CAPTCHA/WAF challenge solving.
- Circumventing anti-bot protections or cryptographically defeating clearance tokens.
- Connecting to the user's local browser from this container without an explicit remote-debugging endpoint.
- Persisting browser profiles, passwords, or arbitrary local browsing history.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Existing post-navigation wait | 3s | USER / prior feature contract |
| Existing post-click wait | 3s | USER / prior feature contract |
| Proposed manual observation window | 30s | DESIGN-FRESH |
| Proposed click hold | 2.5s | USER-provided comparison |
| Maximum automated verification attempts | 1 | Existing feature contract |

## Success Metrics

- Unit tests prove frame selection, actionability logging, bounded click behavior, post-click acceptance, redaction, and artifact lifecycle.
- A headed probe can be run locally and leaves enough evidence for an operator to report what appeared before, during, and after manual interaction.
- `ruff`, `mypy`, and `pytest` pass without storing challenge cookies or credentials in source control.

## Open Questions

- Whether the rendered verification control is a semantic button/link or only text in a challenge widget remains to be established by a headed capture. Owner: operator.
- Whether AtHome's challenge can be manually completed from an authorized local session remains an external-system behavior, not an automation requirement. Owner: operator.
