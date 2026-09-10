# Plan: OpenCodeGo session headers

## Global Constraints

- Keep credentials and session identifiers out of logs, fixtures, and committed artifacts.
- Keep third-party behavior inside the concrete OpenCodeGo adapter.
- Preserve the OpenRouter request contract.
- Verify with ruff, strict mypy, focused unit tests, and one bounded live probe.

## Steps

1. Add a protected request-header seam to the shared OpenAI-compatible transport.
2. Generate one UUID session ID per `OpenCodeGoProvider` instance.
3. Add OpenCodeGo-specific `User-Agent` and `x-opencode-session` headers.
4. Add regression tests for presence, stability, and provider isolation.
5. Run the focused tests and a live CLI probe using a freshly loaded `.env`.
