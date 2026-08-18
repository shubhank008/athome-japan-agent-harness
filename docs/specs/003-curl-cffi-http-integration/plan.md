# Plan: curl-cffi HTTP Scraper Integration

## Files to touch

- `src/athome_harness/scraping/http_adapter.py`: replace httpx request/session use with curl-cffi while retaining the `BaseScraper` behavior and block policy.
- `tests/unit/test_http_adapter.py`: replace httpx/respx transport assumptions with a deterministic curl-cffi session seam and assert impersonation, timeout, cookies, headers, and proxy binding.
- `tests/live/test_playwright_curl_live.py`: add a live-marked three-detail-page handoff integration test.
- `README.md`: document the main adapter's curl-cffi profile, handoff wiring, and live test command.
- `PLAN.md`: record the integration decision and feature status.
- `docs/specs/003-curl-cffi-http-integration/*`: this spec, implementation plan, and marker contract.

## Implementation order

1. Add the spec, plan, and marker contract before code changes.
2. Define the adapter's small curl session/response protocol and migrate `_http_get` to curl-cffi request kwargs.
3. Preserve direct-first proxy rotation for non-handoff sessions and make handoff sessions fail closed on block.
4. Update unit tests with a deterministic injected session that exercises real adapter logic without external network.
5. Add the live Playwright-to-curl-cffi test using three detail URLs extracted from the broad search response, a 2-second request timeout, and `parse_detail_page`.
6. Update README and PLAN, then run pytest, live test when available, ruff, mypy, and whitespace checks.

## Global Constraints

- Preserve `BaseScraper`, `BlockDetected`, challenge detection, proxy-provider, parser, and redacted marker contracts.
- Keep third-party HTTP imports inside concrete adapters; do not put curl-cffi in business models or interfaces.
- Handoff-bound cookies are sensitive: never log cookie values or credentialed proxy URLs, and never commit debug artifacts.
- Do not solve or bypass puzzle challenges programmatically; persistent challenges must remain typed failures.
- Production dependencies remain exact-pinned in `requirements.txt`.
- Verification outside pytest uses `PYTHONPATH=src`.
- Use the existing `AGENTS.md` three-strike and mandatory-pivot rules.

## Evidence

- Unit logs and assertions cover `[CURL_REQUEST]`, `[CURL_HANDOFF_BOUND]`, `[CURL_BLOCK_REHANDOFF]`, and existing block markers.
- The live test emits `[CURL_PLAYWRIGHT_INTEGRATION] pages=<3> timeout=<2>` only after all three detail pages have been fetched and parsed successfully.
- No challenge body is saved as a fixture, and live failures identify the blocked/rendered state without secrets.
