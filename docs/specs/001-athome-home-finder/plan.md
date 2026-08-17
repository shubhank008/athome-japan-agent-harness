# Plan 001: AtHome Japan Home Finder

Order of work, files to touch, and the invariants this feature could violate.
Task IDs are stable; commit messages reference them (e.g., `feat(001-T12): ...`).

## Global Constraints (from AGENTS.md)

Invariants this feature could plausibly violate, read before starting:

- **Abstract First:** scrapers, LLM providers, data stores, evaluators, proxies all
  route through base interfaces. No third-party import in business logic.
- **Focused semantic commits:** specs, contracts, implementation slices, tests, and docs
  land as separate commits where independently meaningful. Never one giant commit.
- **ALWAYS COMMIT AFTER WORK, BEFORE REPORTING.** A run with uncommitted work is incomplete.
- **Never push directly; `/no-mistakes` is the only publish path.** This session stops
  at local commits; push/PR happens only via `/no-mistakes` when the user asks.
- **Comment every method and important code; English everywhere; no em-dashes, no emojis.**
- **`.env.example` must stay in sync with every runtime config key** added by any task.
- **requirements.txt is exact-pinned**; adding deps requires deliberate pinning.
- Marker contract (contracts/log-markers.md) is written before implementation; tests
  grep for markers verbatim.

## Architecture shape

```
src/athome_harness/
  cli.py                    # conversational loop entry
  config.py                 # env parsing, budgets, .env.example sync
  models.py                 # pydantic: SearchPlan, ListingSummary, ListingDetail,
                            #   Recommendation, FilterMap, RunReport
  llm/
    base.py                 # BaseLLMProvider
    openrouter.py           # OpenRouter impl (httpx)
    query_parser.py         # NL query -> SearchPlan (hard filters + soft prefs)
    shortlister.py          # harvest -> top X (batched scoring)
    recommender.py          # details -> top Y report (md + json)
  filters/
    map_schema.py           # versioned filter-map schema + validation
    encoder.py              # SearchPlan -> AtHome POST params via filter map
  scraping/
    base.py                 # BaseScraper, BlockDetected, ProxyProvider iface
    http_adapter.py         # httpx + selectolax DOM adapter
    playwright_adapter.py   # scaffold only, conforms to BaseScraper
    rate_limiter.py         # global polite limiter + jitter
    list_parser.py          # results HTML -> ListingSummary (multi-unit support)
    detail_parser.py        # detail HTML -> ListingDetail
    harvester.py            # pagination engine, budgets, partial results
    proxy/
      base.py               # ProxyProvider interface
      webshare.py           # Webshare rotating proxies
  store/
    base.py                 # BaseDataStore
    sqlite_store.py         # listings, searches, recs, saves, rejects, cache meta
  eval/
    base.py                 # BaseFloorPlanEvaluator
    text_eval.py            # TextDescriptionFloorPlanEvaluator (default)
    vision_eval.py          # VisionFloorPlanEvaluator (stub, off)
  cache/
    prefetch.py             # optional freshness-sorted prefetch worker (post-MVP)
    revalidator.py          # background dead-listing checker (post-MVP)
tools/
  dump_filter_map.py        # extraction tool (US-006)
.github/workflows/
  filter-map.yml            # weekly Action, commits map or files issue
tests/
  unit/...                  # one file per concrete class
  e2e/test_search_session.py# scripted human-like session, asserts markers
  fixtures/                 # saved HTML pages (list, detail) as parse fixtures
docs/specs/001-athome-home-finder/
  spec.md plan.md contracts/log-markers.md
PLAN.md                     # repo-level live plan, updated after every feature
```

## Milestones and granular tasks

### M0: Project skeleton and hygiene

- [ ] T01 `pyproject.toml` (ruff, mypy strict-ish, pytest config), `requirements.txt`
  exact-pinned: httpx, selectolax, beautifulsoup4, pydantic, pytest, pytest-asyncio (if
  needed). Python 3.12.
- [ ] T02 `.env.example` with `OPENROUTER_API_KEY`, `WEBSHARE_PROXY_USER`,
  `WEBSHARE_PROXY_PASS`, budget knobs; `config.py` parser that fails loudly on unknown
  env keys (keeps template and parser in sync per invariant).
- [ ] T03 `models.py` core pydantic models with docstrings; unit tests for model
  invariants (e.g., price breakdown total consistency).
- [ ] T04 `config.py` budgets object (rate limit, max pages, runtime, tokens) with
  defaults from spec's Numeric Values table; unit tests.

### M1: Scraper core (Abstract First)

- [ ] T05 `scraping/base.py`: `BaseScraper` (fetch_html, fetch_binary), `BlockDetected`
  exception carrying detection signature, `ProxyProvider` protocol. Unit test the
  contract with a fake adapter.
- [ ] T06 `scraping/rate_limiter.py`: token-bucket with jitter, injectable clock for
  tests. Unit tests prove spacing and jitter bounds.
- [ ] T07 `scraping/http_adapter.py`: httpx client, browser-like headers, retry with
  exponential backoff, block detection (403/429/captcha markers), proxy hook point.
  Unit tests with `respx`-style transport mocking; integration test marked `live`.
- [ ] T08 `scraping/playwright_adapter.py`: scaffold implementing `BaseScraper` that
  raises `NotImplementedError` with a clear message; contract test ensures interface
  conformance so the swap is drop-in later.
- [ ] T09 `scraping/proxy/base.py` + `proxy/webshare.py`: endpoint list from env,
  rotation policy (try direct first, rotate on block, budget 3). Unit tests with fake
  transport; no live proxy calls in CI.

### M2: Filter map

- [ ] T10 `filters/map_schema.py`: versioned schema (flow -> filter name -> [{code,
  label}]), validation rules (required filters per flow, code regex `kc\d+`, non-empty
  labels, monotonic price ordering sanity check). Unit tests.
- [ ] T11 `tools/dump_filter_map.py`: fetches reference pages (rental Osaka, purchase
  Tokyo as canaries), extracts selects/options into the map JSON, validates, writes
  `filters/data/filter_map.v1.json` with content hash. `--check` mode exits non-zero on
  schema failure and prints an issue-ready report.
- [ ] T12 `.github/workflows/filter-map.yml`: weekly schedule + manual dispatch; commits
  updated map or opens a GitHub issue with the failure diff (uses GITHUB_TOKEN).
- [ ] T13 `filters/encoder.py`: SearchPlan + map -> POST params; raises
  `UnknownFilter`/`UnknownFilterValue` on anything unmappable. Property-based unit tests
  against the checked-in map snapshot.

### M3: Parsing

- [ ] T14 Capture fixtures: save one real list page (Osaka rental) and 2-3 detail pages
  into `tests/fixtures/` (sanity-checked, no personal data). Document capture date.
- [ ] T15 `scraping/list_parser.py`: heading block + per-unit sub-blocks ->
  `ListingSummary` list; multi-unit buildings yield one summary per unit sharing a
  building identity. Unit tests against fixtures incl. detached-house edge case (no
  room number) and missing optional fields (parse warnings recorded, FR-8).
- [ ] T16 `scraping/detail_parser.py`: full field extraction + photo URLs + floor-plan
  image URL + USP tags -> `ListingDetail`. Unit tests against fixtures.

### M4: LLM layer

- [ ] T17 `llm/base.py`: `BaseLLMProvider` (complete_json with schema validation,
  token accounting). Contract test with fake provider.
- [ ] T18 `llm/openrouter.py`: OpenRouter impl via httpx, model configurable, temp 0,
  JSON-mode with repair-and-retry-once. Unit tests with mocked transport.
- [ ] T19 `llm/query_parser.py`: NL -> SearchPlan; loads filter map summary into prompt;
  ambiguity triggers `ClarificationNeeded`. Unit tests with canned LLM responses (no
  network), incl. rent-vs-buy intent split.
- [ ] T20 `llm/shortlister.py`: batched scoring of ListingSummary batches against soft
  prefs, ordered top-X with rationales. Token budget enforced. Unit tests with fake
  provider; determinism test (same input -> same output).
- [ ] T21 `llm/recommender.py`: details -> top-Y reasons + violated constraints;
  renders markdown + JSON report. Golden-file tests.

### M5: Store

- [ ] T22 `store/base.py`: `BaseDataStore` (upsert_listing, record_search,
  record_recommendation, save, reject, history queries). Contract test suite reusable
  by future backends.
- [ ] T23 `store/sqlite_store.py`: schema v1 (listings keyed by internal ID with
  AtHome `BKLISTID` + URL dedupe, searches, recommendations, saves, rejects, cache_meta
  for US-009). Migration helper. Unit tests on temp DB, passing the T22 contract suite.

### M6: Orchestration and CLI

- [ ] T24 `scraping/harvester.py`: pagination engine over BaseScraper; budget checks
  each page; partial-results summary on abort; marker logging per contract. Unit tests
  with fake scraper serving fixture pages.
- [ ] T25 `cli.py`: conversational loop wiring parser -> plan confirm -> harvest ->
  top-X preview -> detail scrape -> report -> feedback commands (`save N`, `reject N`,
  `more like N`, `refine ...`). Unit tests per command handler.
- [ ] T26 `tests/e2e/test_search_session.py`: scripted human-like session against
  fixtures + fake LLM: runs the full loop, asserts every contract marker appears and no
  failure patterns appear, asserts report files exist and parse.

### M7: Maintenance surfaces

- [ ] T27 US-008 integration test: simulated block -> proxy rotation markers appear in
  order, direct-first proven.
- [ ] T28 Docs: README quickstart (env, install, first search), architecture section
  linking spec/plan/contract; PLAN.md at repo root created and updated.

### M8: Post-MVP (spec'd, scheduled later)

- [ ] T29 US-009 `cache/prefetch.py` + `cache/revalidator.py` behind config flag.
- [ ] T30 US-010 vision evaluator stub + A/B benchmark harness + report.
- [ ] T31 US-007 purchase-flow fixtures + parser diffs + map coverage (may pull forward
  if cheap after M3 reveals how different purchase markup is).

## Risks and mitigations

- AtHome DOM drift -> weekly filter-map Action (T12) + parse-warning telemetry (T15/T16).
- IP block mid-search -> T07/T09 rotation with bounded retries; partial report (T24).
- LLM JSON misalignment -> schema validation + repair-once (T18); contract failure
  pattern `LLM_JSON_INVALID` monitored in e2e.
- Scope creep toward product features -> Non-Goals section is the fence; WebUI and
  multi-user arrive as their own specs.
