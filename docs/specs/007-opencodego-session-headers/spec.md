# Spec: OpenCodeGo session headers

> A live home-finder search can use OpenCodeGo because each provider conversation identifies the application and supplies a stable session ID required for routing.

## Context

The OpenCodeGo endpoint rejected a valid, supported `deepseek-v4-flash` request with `MissingSessionID`. The provider documentation supplied by the user requires clients to identify themselves and send a stable `x-opencode-session` value for each conversation.

## User Stories

### US-001: Run an OpenCodeGo-backed search
**Description:** As an operator, I want the configured OpenCodeGo provider to send the required routing headers so that a live CLI search can proceed beyond provider validation.

**Acceptance Criteria:**
- [ ] Each OpenCodeGo completion includes an application-specific `User-Agent`.
- [ ] Each OpenCodeGo completion includes `x-opencode-session`.
- [ ] The session value remains unchanged for repeated completions from one provider instance.
- [ ] Separate provider instances use separate session values.
- [ ] OpenRouter continues to send only its existing default request headers.

## Functional Requirements

- FR-1: OpenCodeGo must generate a session ID when the provider instance is created.
- FR-2: The session ID must be reused for every completion sent by that instance.
- FR-3: The session ID and authorization credential must never be logged.

## Non-Goals

- Persisting or sharing an OpenCodeGo session ID across application processes.
- Changing model selection, API keys, or endpoint configuration.
- Changing OpenRouter request headers.

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Session IDs per provider instance | 1 | USER |

## Success Metrics

Focused unit tests prove the exact headers and their session stability, and a fresh live CLI run no longer receives `MissingSessionID`.

## Open Questions

None.
