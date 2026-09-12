# Plan: Bounded LLM transport retry and recommender diagnostics

## Work order

1. Add stable marker and debug artifact contract.
2. Add provider transport retry with bounded delay.
3. Add recommender-stage request/response capture.
4. Add unit tests for timeout recovery, terminal failure, and overwrite behavior.
5. Run ruff, mypy, and pytest; update reference docs and SPEC status.

## Files

- `src/athome_harness/llm/openai_compat.py`
- `src/athome_harness/llm/base.py`
- `src/athome_harness/llm/recommender.py`
- `tests/unit/test_openrouter.py`
- `tests/unit/test_recommender.py`
- `docs/reference/llm.md`

## Global Constraints

- Retry is bounded and must not hide terminal provider failures.
- Do not log credentials, cookies, proxy URLs, or session headers.
- Keep JSON/schema repair retry separate from transport retry.
- Debug artifacts overwrite ignored local files.
- Do not automate or bypass AtHome challenges.
