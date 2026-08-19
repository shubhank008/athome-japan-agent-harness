# Spec: Lean Production Cookie Fetcher

The production Patchright cookie farmer renders AtHome once, optionally performs a single human-like verification click, and persists only the two artifacts curl-cffi workers need: the typed `CookieHandoff` JSON (plus `cookies.txt`) and the `session_state.json` snapshot. It captures no screenshots, browser trace, WebM video, or structured event log. That diagnostic capture now lives exclusively in the operator `scripts/playwright_manual_probe.py` DEBUG mode, so the production fallback path stays lean and fast.

## Context

Spec 004 (playwright-challenge-diagnostics) embedded screenshot/trace/video/event capture directly inside `PlaywrightCookieFetcher`. Review of the operator probe shows it already owns the entire diagnostics surface (its own `_event` JSONL log, trace stop, video finalize, and before/after screenshots). Keeping the same machinery in the production adapter duplicates code, slows every refarm, and adds failure modes (trace stop, video finalize) that have nothing to do with producing a session. This change removes the duplication so the adapter is single-purpose.

Related: spec 002 (cookie fetcher), spec 003 (curl-cffi integration), spec 004 (diagnostics, now probe-only), spec 005 (patchright runtime).

## User Stories

### US-001: Farm a lean handoff in production
**Description:** As the refarm orchestration layer, I want the cookie farmer to produce a handoff and session_state without diagnostic capture so that challenge recovery is fast and has no extraneous failure modes.

**Acceptance Criteria:**
- [ ] `PlaywrightCookieFetcher` has no `diagnostics` constructor parameter and never records video, trace, screenshots, or an event log.
- [ ] A successful `farm()` still writes `cookie_handoff_<proxy>.json`, `cookies.txt`, and `session_state.json`.
- [ ] `farm()` still performs the single verification click when AtHome serves a challenge, and still rejects a challenge that remains after the click.

### US-002: Diagnose challenges via the probe
**Description:** As an operator, I want all visual/trace diagnostics to come from the manual probe DEBUG mode so that there is exactly one place that owns that overhead.

**Acceptance Criteria:**
- [ ] `scripts/playwright_manual_probe.py` retains its screenshots, trace, video, and event log unchanged.

## Functional Requirements

- FR-1: Remove the `diagnostics` flag and every code path it gates (context video options, tracing start/stop, `_event` JSONL, `_artifact_names`, `_finalize_video`, `_save_debug_capture`).
- FR-2: Remove the now-unused diagnostic artifact path attributes and the `Video` import.
- FR-3: Preserve the request-header capture, stealth hook, challenge detect/click/reject flow, handoff build/save, and `session_state.json` persistence exactly.
- FR-4: Keep the `[PATCHRIGHT_*]`, `[PLAYWRIGHT_CHALLENGE]`, `[PLAYWRIGHT_VERIFY*]`, `[PLAYWRIGHT_RENDERED]`, `[PLAYWRIGHT_HANDOFF_*]`, and `[PLAYWRIGHT_SESSION_STATE_SAVED]` log markers so log-based verification still works.

## Non-Goals

- No change to challenge-solving behavior (the farmer still never solves puzzles; it performs one bounded click only).
- No change to `SessionState`, `SessionRefarmer`, or the probe behavior.
- No headed/interactive mode in the production adapter (that is the probe's role).

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Verification click hold time | 2.5s | VERIFIED (existing `DEFAULT_CLICK_HOLD_SECONDS`, unchanged) |
| Default post-render wait | 3.0s | VERIFIED (existing `DEFAULT_WAIT_SECONDS`, unchanged) |
| Min rendered HTML length | 200 chars | VERIFIED (existing `MIN_RENDERED_HTML_LENGTH`, unchanged) |

## Success Metrics

- Unit tests for the fetcher pass with no `diagnostics=` argument anywhere.
- `farm()` on a rendered page persists the handoff and session_state and produces no `playwright_challenge_trace.zip`, `playwright_challenge.webm`, `playwright_events.jsonl`, or `playwright_*.png/html`.
- The probe DEBUG mode still emits all diagnostic artifacts.

## Open Questions

- None. The probe already owns diagnostics; this only deletes the duplicate.
