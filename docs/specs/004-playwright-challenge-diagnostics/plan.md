# Plan: Playwright Challenge Diagnostics

## Files to touch

- `src/athome_harness/scraping/playwright_cookie_fetcher.py`: add bounded diagnostics, frame-aware control discovery, structured redacted event logging, trace/video lifecycle, and manual observation configuration.
- `src/athome_harness/scraping/challenge.py`: extend marker coverage only for observed verification wording variants.
- `tests/unit/test_playwright_cookie_fetcher.py`: cover diagnostics, frame fallback, redaction, and click/post-click outcomes using browser-independent fakes.
- `tests/live/test_playwright_curl_live.py`: retain the existing opt-in live handoff test and add a separately opt-in diagnostic/manual probe entry point if practical.
- `scripts/playwright_manual_probe.py`: provide a local headed probe that lets an operator manually inspect the challenge from the operator's own network.
- `README.md`: document video/trace artifacts, manual probe usage, and local-network limitations.
- `PLAN.md`: record the diagnostics milestone and safety boundary.
- `docs/specs/004-playwright-challenge-diagnostics/*`: this spec, plan, and marker contract.

## Implementation order

1. Write the marker contract and diagnostic artifact policy.
2. Add injectable diagnostic configuration and redacted JSON-lines logging.
3. Add iframe-aware target discovery and robust bounded click attempt without puzzle dragging.
4. Add Playwright video and trace lifecycle, including cleanup on failures.
5. Add the local headed manual probe and deterministic unit tests.
6. Update README and project plan.
7. Run pytest, ruff, mypy, and inspect generated artifacts and diff.

## Global Constraints

- Do not automate puzzle-piece dragging, CAPTCHA solving, WAF cryptanalysis, or anti-bot bypass.
- Preserve the existing challenge detector and fail-closed handoff behavior.
- Do not log cookie values, proxy credentials, or full query-bearing URLs.
- Keep Playwright imports inside the concrete adapter and manual probe.
- Keep production requirements exact-pinned and avoid new dependencies where Playwright already provides the capability.
- Use `PYTHONPATH=src` for verification commands outside pytest.
- Keep debug artifacts ignored and never commit captures.
- A failed diagnostic farm must not overwrite a last successful handoff.
- The manual probe must run on the operator's machine to use the operator's public IP; the container cannot transparently turn its browser into the user's browser.

## Evidence

- Unit tests assert every required diagnostic marker and redaction rule.
- Unit tests assert child-frame control discovery and post-click challenge rejection.
- A local headed run produces WebM, trace ZIP, before/after HTML, before/after PNG, and JSONL evidence under `debug/`.
- The final report names any external WAF behavior that remains unresolved.
