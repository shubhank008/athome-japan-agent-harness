# Plan: Playwright Cookie Fetcher

## Files to touch

- `src/athome_harness/scraping/cookie_handoff.py`: typed, serializable browser-to-curl
  handoff and sanitized proxy identity helpers.
- `src/athome_harness/scraping/playwright_cookie_fetcher.py`: concrete async Playwright
  farmer, stealth application, challenge capture, verification click, and browser cleanup.
- `src/athome_harness/scraping/challenge.py`: shared AtHome challenge markers and detector.
- `src/athome_harness/scraping/http_adapter.py`: consume the shared detector while keeping
  the existing private compatibility function.
- `requirements.txt`, `README.md`: exact dependencies and browser installation instructions.
- `.gitignore`: preserve the existing debug-artifact ignore rule.
- `tests/unit/test_cookie_handoff.py`: persistence, redaction, and curl-cffi kwargs.
- `tests/unit/test_playwright_cookie_fetcher.py`: fake Playwright state-machine tests.
- `docs/specs/002-playwright-cookie-fetcher/*`: this spec, plan, and marker contract.
- `PLAN.md`: record feature status and decisions.

## Implementation order

1. Extract the existing challenge detector into the shared scraper module without
   changing its public behavior.
2. Implement the immutable handoff object and JSON/cookie-header persistence.
3. Implement the async farmer behind injectable browser and filesystem seams so tests
   do not require a live browser.
4. Add exact-pinned dependencies and README setup steps.
5. Add unit and browser-independent e2e-style tests.
6. Run tests, ruff, and mypy; inspect that only intended files changed.

## Global Constraints

- Preserve `BaseScraper` and `BlockDetected` contracts and redacted marker rules.
- Keep third-party imports in concrete adapter modules; handoff models use the standard
  library only.
- Never save an AtHome challenge page as usable data or solve a puzzle programmatically.
- Do not log proxy credentials, cookies, or full query-bearing URLs.
- Keep the existing user `.gitignore` modification intact.
- `requirements.txt` production dependencies remain exact-pinned.
- Verification outside pytest uses `PYTHONPATH=src`.
- A failed browser farm must not replace a valid local handoff.

## Evidence

- Fake-browser tests assert before/after HTML and screenshot paths are requested on
  challenge handling.
- Tests assert `[PLAYWRIGHT_FARM_START]`, `[PLAYWRIGHT_CHALLENGE]`,
  `[PLAYWRIGHT_VERIFY]`, and `[PLAYWRIGHT_HANDOFF_SAVED]` markers, plus absence of
  credential-bearing output.
- A live browser smoke test is optional and marked `live` because browser binaries and
  network access are environment-dependent.
