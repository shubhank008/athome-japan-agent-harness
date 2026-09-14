# Spec: Bounded LLM transport retry and recommender diagnostics

When an LLM request fails transiently, the harness makes a small bounded number
of additional attempts before surfacing the failure. Operators can inspect the
final recommender request and response locally when DEBUG is enabled.

## Context

The final recommendation call timed out after the configured 300-second transport
timeout. The existing retry handles only invalid JSON, not transport failures.

## User Stories

### US-001: Recover transient LLM transport failures
**Description:** As an operator, I want transient provider failures retried so a
single timeout does not discard an otherwise successful search.

**Acceptance Criteria:**
- [x] Transport retry is bounded and applies to the final recommender call.
- [x] JSON/schema repair retry remains distinct from transport retry.
- [x] Retry attempts and terminal failure are observable without secrets.
- [x] Existing provider callers retain compatible behavior.

### US-002: Inspect recommender payloads
**Description:** As an operator, I want the final recommender request and raw
response captured locally so prompt size and output quality can be evaluated.

**Acceptance Criteria:**
- [x] DEBUG captures stable overwritten request and response artifacts.
- [x] Captures include stage and token metadata where available.
- [x] Transport failures preserve request diagnostics without fabricating output.
- [x] Credentials, session headers, and proxy URLs are excluded.

## Functional Requirements

- FR-1: Retry only classified transient transport failures.
- FR-2: Use at most two total transport attempts per completion.
- FR-3: Use a bounded one-second delay between attempts.
- FR-4: Emit stable retry and terminal failure markers.
- FR-5: Preserve usage accounting for successful attempts.
- FR-6: Keep debug files under ignored local paths and overwrite them.

## Non-Goals

- No CAPTCHA/WAF bypass.
- No unbounded retry loop.
- No remote upload of prompts or outputs.
- No retry of schema-invalid responses through the transport retry mechanism.

## Numeric Values

| Value | Number | Source |
|---|---:|---|
| Total transport attempts | 2 | DESIGN-FRESH |
| Retry delay | 1 second | DESIGN-FRESH |

## Success Metrics

A simulated timeout followed by success completes the same completion call and
records the retry. A terminal timeout remains a clear provider error. Recommender
DEBUG artifacts contain the actual request and successful raw response.

## Open Questions

- Whether provider-specific retryable HTTP status codes should be configurable.
