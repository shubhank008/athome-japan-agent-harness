# Plan: Patchright Browser Runtime

## Files to touch

- `src/athome_harness/scraping/playwright_cookie_fetcher.py`: import Patchright and use a temporary persistent Chrome context with the existing stealth compatibility hook.
- `scripts/playwright_manual_probe.py`: import Patchright and use the same persistent Chrome configuration.
- `tests/unit/test_playwright_cookie_fetcher.py`: update browser fakes for persistent-context lifecycle and preserve behavior coverage.
- `requirements.txt`: replace the Playwright runtime pin with the exact Patchright pin and retain Playwright-Stealth.
- `README.md`: document Patchright installation and Chrome requirements.
- `docs/specs/005-patchright-runtime/*`: record the runtime contract and markers.

## Implementation order

1. Write the spec and marker contract.
2. Switch concrete imports while preserving the stealth compatibility hook.
3. Change browser lifecycle to temporary persistent Chrome contexts.
4. Update deterministic unit fakes and assertions.
5. Update dependency and operator documentation.
6. Run tests, Ruff, mypy, and a safe live startup check if the host supports Chrome.

## Global Constraints

- Preserve exact proxy, cookie, and user-agent handoff binding.
- Do not inject custom browser headers or user-agent strings.
- Do not automate puzzle-piece dragging, CAPTCHA solving, or WAF bypass.
- Production dependencies remain exact-pinned.
- Debug captures can contain session data and must not be committed.
- Use `PYTHONPATH=src` for verification outside pytest.
- Do not use a blanket Git restore that could erase user work.

## Evidence

- Unit tests cover persistent context options and browser/context cleanup.
- The dependency import check resolves `patchright.async_api`.
- Ruff, mypy, and the complete pytest suite pass.
- A live run remains opt-in because it contacts the authorized AtHome service; no live run was performed.
