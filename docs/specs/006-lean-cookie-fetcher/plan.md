# Plan: Lean Production Cookie Fetcher

## Files to touch, in order

1. `src/athome_harness/scraping/playwright_cookie_fetcher.py` — remove diagnostics machinery.
2. `tests/unit/test_playwright_cookie_fetcher.py` — update tests to the lean surface.
3. Gate: ruff check, ruff format, mypy strict (src), pytest.

## Order of work

1. In the fetcher: drop `diagnostics` param, `self._diagnostics`, the diagnostic path attributes and constants, the `Video` import, the video branch in `_context_options`, the trace start/stop, every `self._event(...)` call, `_save_debug_capture`, `_artifact_names`, `_finalize_video`, and the trace/video finalize + diagnostics-saved block in `finally`.
2. Keep `context.close()` (still required) but drop the trace/video-specific cleanup logging markers.
3. In the tests: remove `diagnostics=` kwargs and every assertion about trace/video/events/screenshot artifacts; keep handoff, session_state, cookies.txt, click, and reject assertions. Replace the diagnostics-emitting tests with default-path tests that assert the absence of those artifacts.

## Global Constraints (AGENTS.md invariants this could violate)

- Abstract-first: the fetcher still produces the abstract `CookieHandoff`; no third-party service is hardcoded beyond the existing Patchright launch. Unaffected.
- Gatekeeping: ruff + mypy strict + pytest must all pass before commit. Removing methods will create unused imports (`json`, `Video`) — these must be removed to satisfy ruff `F401`.
- Marker contract: failure patterns must NOT appear (see contracts/log-markers.md). The negative markers are new "must not appear" assertions for the lean path.
- Commit protocol: spec/plan/contract committed separately from the implementation slice.

## Risks

- Tests `test_farm_prefers_semantic_control_in_child_frame` and `test_cleanup_failures_do_not_mask_render_error` lean heavily on events/trace; they must be rewritten to assert behavior (click happened, challenge rejected) rather than artifacts.
