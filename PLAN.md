# PLAN

Live project plan. Updated after every feature or update, per AGENTS.md.

## Current state

M0 (project skeleton + hygiene), M1 (scraper core), M2 (filter map), M3 (parsing),
M4 (LLM layer), M5 (store), M6 (orchestration + CLI), M7 (maintenance
surfaces), current live DOM migration, structured server-state parsing, runtime
DEBUG diagnostics, money/age semantics, detail metadata enrichment, geography-aware
routing, building/unit design, and production timing improvements are implemented
locally and committed. Publication and final no-mistakes verification remain pending.
M0: `config.py` (strict env parser + `Budgets`), `models.py` (pydantic data models),
`pyproject.toml` + exact-pinned `requirements.txt`. M1: `scraping/base.py`
(`BaseScraper`, `BlockDetected`, `ProxyProvider`), `scraping/rate_limiter.py`
(token-bucket with jitter), `scraping/http_adapter.py` (curl-cffi + selectolax DOM
adapter with block detection, proxy rotation, and AtHome challenge detection),
`scraping/playwright_adapter.py` (scaffold), `scraping/proxy/base.py` +
`scraping/proxy/webshare.py` (proxy rotation policy). M2: `filters/map_schema.py`
(versioned schema + validation with missing-flow rejection), `filters/encoder.py`
(SearchPlan -> POST params), `tools/dump_filter_map.py` (extraction tool),
`.github/workflows/filter-map.yml` (weekly refresh), checked-in
`filters/data/filter_map.v1.json`. M3: `scraping/list_parser.py` (results HTML ->
`ListingSummary` list), `scraping/detail_parser.py` (detail HTML -> `ListingDetail`),
live-captured fixtures in `tests/fixtures/`. M4: `llm/base.py` (BaseLLMProvider with
schema-validated completion, token accounting, exactly-one repair retry),
`llm/openrouter.py` (OpenAI-compatible transport base with OpenRouter and OpencodeGo
providers via curl-cffi, injectable session),
`llm/query_parser.py` (NL -> SearchPlan with rent/buy flow resolution and clarification),
`llm/shortlister.py` (token-bounded batched scoring, ordered top-X with rationales),
`llm/recommender.py` (top-Y ranking with reasons and violated constraints, markdown +
JSON reports with golden-file tests). `ATHOME_LLM_MAX_TOKENS` budget added to config and
`.env.example`. M5: `store/base.py` (BaseDataStore contract), `store/sqlite_store.py`
(SQLite backend with listings, searches, recommendations, saves, rejects, cache_meta).
M6: `scraping/harvester.py` (budget-aware pagination engine with partial results and
marker logging), `cli.py` (typed conversational CLI/REPL with dependency injection,
search, save/reject/more like/refine feedback commands), scripted e2e test session.
M7: `tests/e2e/test_proxy_fallback.py` (T27) drives the real `SessionRefarmer` +
`HttpDomAdapter` boundary with a fake curl session and fake async farmer, asserting
the direct-first then bounded farm/rebind recovery and the marker contract order
(`[BLOCK_DETECTED] -> [REHANDOFF_TRIGGERED] -> [REHANDOFF_FARMED] ->
[CURL_HANDOFF_BOUND]`); refarm orchestration markers were added to the marker
contract. Documentation (T28) added a README quickstart and architecture section
and updated this plan and the feature plan/contract.
M3 parser hardening remains pending for building age, normalized building type,
month-based deposit terms, detail disabled-feature coverage, and the required
second/third detail fixtures. Feature 002 adds the Patchright cookie farmer and typed
curl-cffi handoff; Feature 006 adds lean production refarming. Merged `origin/main`
currently passes 300+ unit tests, ruff, and mypy.

## Active feature

| Feature | Spec | Status |
|---------|------|--------|
| 001 AtHome Home Finder | `docs/specs/001-athome-home-finder/` (spec, plan, marker contract) | M0-M7 done |
| 002 Playwright Cookie Fetcher | `docs/specs/002-playwright-cookie-fetcher/` | merged through PR #7; security boundary and live behavior require ongoing review |
| 003 curl-cffi HTTP Integration | `docs/specs/003-curl-cffi-http-integration/` | merged through PR #7; bounded refarm path implemented |
| 004 Playwright Challenge Diagnostics | `docs/specs/004-playwright-challenge-diagnostics/` | merged through PR #7; operator diagnostics path implemented |
| 005 Patchright Runtime | `docs/specs/005-patchright-runtime/` | merged through PR #7; live challenge behavior remains operationally constrained |
| 006 Lean Cookie Fetcher | `docs/specs/006-lean-cookie-fetcher/` | merged through PR #7; production diagnostics reduced to handoff/session state |
| 007 OpenCodeGo session headers | `docs/specs/007-opencodego-session-headers/` | implemented locally; stable coding-agent headers and live provider handshake verified |

## Feature 001 summary

Conversational CLI that turns natural-language housing wishes into ranked rental and
purchase recommendations from athome.co.jp. Funnel: NL query -> SearchPlan -> AtHome
filter encoding (versioned filter map) -> full harvest of filtered results -> LLM
shortlist (top X) -> detail scrape -> top-Y report (markdown + JSON) -> persistent
memory (seen/saved/rejected). Abstract-first: BaseScraper (curl-cffi adapter now,
Playwright scaffold), PlaywrightCookieFetcher (async browser farmer producing a
typed CookieHandoff), SessionRefarmer (production fallback loop orchestrating
HttpDom -> block -> browser farm -> rebound HttpDom), BaseLLMProvider (OpenRouter or
OpencodeGo, config-driven), BaseDataStore (SQLite first), BaseFloorPlanEvaluator (text default, vision
stub). Webshare proxy rotation on block detection only. Weekly GitHub Action re-extracts the filter map and files an issue on
DOM drift. Post-MVP: prefetch cache with freshness ordering and dead-listing
revalidation, vision A/B benchmarks.

## Milestone board (001)

| Milestone | Tasks | State |
|-----------|-------|-------|
| M0 Skeleton | T01-T04 | done (2026-07-08, `feat/001-m0-skeleton`, verified) |
| M1 Scraper core | T05-T09 | done (2026-07-08, `feat/001-m1-scraper`, PR #2 merged, independently verified) |
| M2 Filter map | T10-T13 | done (2026-08-17, `feat/001-m2-filter-map`) |
| M3 Parsing | T14-T16 | merged through PR #4; hardening follow-up pending |
| M4 LLM layer | T17-T21 | done (2026-08-19, `feat/001-m4-llm-layer-fresh`) |
| M5 Store | T22-T23 | done (2026-08-19, `feat/001-m5-store`) |
| M6 Orchestration + CLI | T24-T26 | done (2026-08-19, `feat/001-m6-orchestration-cli`) |
| M7 Maintenance surfaces | T27-T28 | done (2026-08-19, `feat/001-m7-maintenance-surfaces`) |
| M8 Configurable providers | (factory) | done (PR #15 `feat/001-m8-configurable-providers`) |
| Post-MVP | T29-T36 | detail hydration catalogue, agency records, vision, and purchase coverage spec'd; not scheduled |

## Decisions log

- 2026-07-08: Live searches scrape 100% of the LLM-filtered result set; broad-net
  coverage for unfiltered exploration is delegated to the optional prefetch cache
  (freshness-sorted), not to live searches. Rationale: 300k-listing prefectures make
  percentage-of-everything live scraping multi-hour and rate-limit hostile.

## Next implementation phases

These phases are intentionally split into one focused commit per task. They are the
next planned work after the current parser/runtime foundation and are not yet
implemented unless marked otherwise.

### Phase A: Diagnostics and observability

- **A1: Transport retry and final-call diagnostics**: add a bounded retry with
  backoff for transient LLM transport failures, including the final recommender
  call. Dump the recommender's raw request payload before transport, and its raw
  response after transport, under stable DEBUG paths. Keep JSON/schema repair retry
  behavior distinct from transport retry behavior. **Completed in `3282db2`.**
- **A2: Stable DEBUG artifact contract**: retain fixed overwrite paths for
  list/detail/LLM artifacts; capture a post-handoff challenge body only when
  `DEBUG=true`; never persist direct challenge bodies by default. Add tests for
  redaction, overwrite behavior, and valid-farmed-session challenge capture.
- **A3: Diagnostic retention roadmap**: design dated debug subdirectories, 14-day
  cleanup, log rotation, and an optional remote log/analysis sink. Do not upload
  local captures without explicit operator authorization. **Future roadmap.**

### Phase B: Detail data contract

- **B1: Structured server-state parser**: validate
  `script#serverApp-state -> first-view-ITEMS.propertyData.rentInfo`, map fields,
  and fall back to current DOM parsing. Completed in `5528fe6`; schema reference
  and sanitized example are in `docs/reference/server-app-state.md`.
- **B2: Financial and age semantics**: keep raw duration terms, make numeric deposit
  fields nullable for non-yen values, preserve construction date and age raw text,
  and expose rounded/human-friendly age. Implemented in `b727bd4`; regression
  coverage and live-schema completeness review remain part of B5.
- **B3: Detail metadata enrichment**: map contract period, building structure,
  total units, remarks, and structured PICK UP enabled/disabled features. Core
  implementation landed in `215dc47`; verify all desired
  `property-summary-main-content` fields against the server-state payload and add
  fixture coverage.
- **B4: Detail validation and hydration**: validate meaningful identity and price
  fields, merge valid detail values onto list summaries, preserve summary values on
  failure, and expose `listing_detail` plus a meaningful failure reason. Completed
  in `b58f981`.
- **B5: Detail schema completeness**: audit the structured payload mapping for
  contract period, construction date, floor, area, building metadata, and raw
  remarks; add missing fields, tests, and sanitized example payload updates.
  **After A1.**

### Phase C: Building-aware domain model

- **C1: Building identity normalization**: define conservative identity keys from
  structured building ID when available, otherwise normalized building name/address.
  Add collision and missing-identity tests. **After B5.**
- **C2: Building and unit models**: introduce aggregate models while retaining every
  unit's room, floor, price, area, contract, availability, and URL fields.
- **C3: Post-detail grouping**: group only after detail hydration and before the
  recommender prompt; preserve every unit and expose representative-unit selection
  without discarding alternatives. This avoids pre-detail collisions and keeps
  floor/price differences visible.
- **C4: Building-aware shortlist/report**: rank at building level only with an
  explicit unit-aware projection, show unit alternatives, and update store
  persistence without breaking save/reject URLs.

### Phase D: Geography and query execution

- **D1: Query-plan reporting**: persist flow, prefecture, cities, hard filters, and
  soft preferences in JSON reports. Implemented in `50592c8`; add explicit stage
  log coverage for parsed plans.
- **D2: Prefecture route resolver**: replace hard-coded Osaka paths with validated
  flow/prefecture route mappings; reject unsupported combinations explicitly.
  **Next after detail/building contracts.**
- **D3: City/area route resolver**: resolve parsed city/area names to verified
  AtHome slugs and encode city context into list requests; add Tokyo, Sapporo,
  and Osaka tests.
- **D4: Multi-region live smoke checks**: run authorized bounded checks for one rent
  and one buy route per supported region; never claim a region is live without
  parser and filter-map evidence.

### Phase E: Performance and correctness

- **E1: Monotonic timing**: use a real production monotonic clock for harvest and
  stage duration logs. Implemented in `f85c94e`.
- **E2: Detail settle selectors**: include `#item-detail_top` and
  `#item-detail.main-area` in the browser signal race; measure timeout reduction.
  Implemented in `f85c94e`.
- **E3: Shortlist payload reduction**: evaluate a compact scoring projection and
  compare token/latency/quality evidence against the full summary projection.
- **E4: Recommender optimization**: constrain reason/constraint output lengths,
  add a recommendation-specific token budget, and consider deterministic ranking
  after shortlist where product quality permits.
- **E5: OpenCodeGo cache-prefix evaluation**: keep system/schema instructions
  static, put dynamic listing data after the static prefix, avoid timestamps or
  UUIDs in prompt prefixes, and measure cache/latency evidence from provider
  responses rather than assuming session IDs guarantee caching.
- **E6: Bounded concurrency tuning**: benchmark two-worker shortlist calls, provider
  throttling, repair frequency, and total wall time before considering higher
  concurrency or larger batches.

### Phase F: Quality gates and maintenance

- **F1: Isolate tests from operator `.env`**: disable dotenv loading in settings
  unit helpers so provider-default tests are deterministic.
- **F2: Current DOM fixture refresh**: maintain validated list/detail captures and
  update the DOM access map plus regression tests together.
- **F3: Full no-mistakes run**: run lint, mypy, tests, documentation review, and
  publication only after each focused phase is committed.

### Phase G: Detail hydration catalogue

- **G1: Listing and agency persistence contract**: add explicit listing completeness
  states (`summary_partial`, `summary_complete`, `detail_complete`), 14-day detail
  freshness metadata, and a deduplicated `Agency` entity keyed by `kaiinNo`. **Implemented
  locally in T29.**
- **G2: Rich structured detail persistence**: store the selected `kaiinInfo` profile
  separately and link it from listings; retain detail transit, facilities, images,
  costs, surrounding data, and source payload for the internal record while keeping the
  LLM projection compact and separately generated. **Implemented locally in T30.**
- **G3: Recommendation-card ingestion**: normalize `otherPropertyData` into existing
  summary records, upsert as `summary_complete`, never downgrade fresh detail, and
  idempotently queue missing/stale detail hydration. **Implemented locally in T31 and
  wired into live detail success.**
- **G4: Durable FIFO hydration queue**: add atomic claim/lease, freshness recheck,
  completion/skip, bounded retry, and deletion only on positively identified unavailable
  detail pages. Block/challenge/timeout/parser failures remain retryable evidence.
  **Implemented locally in T32.**
- **G5: Lean background worker**: provide a config-gated standalone worker that claims,
  fetches, validates, parses, upserts, and acknowledges one job at a time using existing
  rate limits and challenge handling. On blocks/challenges, cool down or stop the
  affected worker/pool and report the condition; never scale around target controls.
  **Implemented locally in T33.**
- **G6: Live-path cache read**: allow the LLM pipeline to use only fresh detail records;
  otherwise it directly fetches and upserts detail without waiting for or sharing the
  background worker path. **Implemented locally in T34.**
- **G7: Deferred search-result cache**: if latency later requires it, cache complete
  normalized search parameter queries briefly and independently from listing details.

- 2026-07-08: robots.txt is honored in spirit (rate limits, session scope) not
  mechanically; user decision, on record.
- 2026-09-12: LLM output budgeting uses a qualitative reasoning effort setting with
  default `low`; the provisional numeric policy is low=2500, medium=5000, high=8000.
  Provider payloads must preserve the single configured `ATHOME_LLM_MAX_TOKENS` value
  as the total ceiling and send it in both `max_tokens` and `max_completion_tokens`.
  Numeric reasoning fields remain internal telemetry until universally supported.
- 2026-09-13: Live OpenCodeGo probes confirmed `glm-5.2` accepts `max_tokens`, `max_completion_tokens`, and `reasoning_effort`, but rejects `max_output_tokens` and nested `reasoning`; `deepseek-v4-flash` accepted all tested variants. To avoid model-specific configuration, the universal payload will send only `max_tokens` and `max_completion_tokens` from `ATHOME_LLM_MAX_TOKENS` plus qualitative `reasoning_effort` (default low). Low/medium/high desired numeric budgets (2500/5000/8000) remain internal policy and telemetry until a universally accepted numeric field exists; never send a field proven to make GLM requests fail.
- 2026-07-08: Filter map is context-keyed by (flow, filter name) because `kcXXX` codes
  collide across PRICEFROM/PRICETO and flows.
- 2026-07-08: Project-scoped PRD.md and SPEC.md live at repo root; the feature spec in
  docs/specs is task-scoped and defers to them for product intent and filter truth.
- 2026-07-08: Models: general `deepseek/deepseek-v4-flash-0731`, vision
  `google/gemma-4-31b-it` (both verified on OpenRouter).
- 2026-07-08: Webshare cheapest plan, per-session proxy rotation, invoked only on a
  real main-IP block.
- 2026-07-08: Prefetch scope Osaka prefecture first, scale to all prefectures slowly.
- 2026-07-08: Conditions map (SPEC.md 1.1) encodes per-field cardinality (single /
  multi / range / bool) so tool-calling knows each parameter's signature; layout and
  other multi filters accept lists.
- 2026-07-08: Disabled features (`p-property__information-facility_disabled-list`) are
  recorded as Probable Negatives and surfaced as caveats, not ignored.
- 2026-07-08: Execution model adopted: main chat orchestrates and evaluates; one
  delegated subagent (local agent-server conversation) implements each milestone
  sequentially. After one dispatch health check, the main chat stops and waits for
  the user to announce completion; it does not poll or burn context while waiting.
  The orchestrator re-runs the gatekeeper and vets reported landmines before accepting.
- 2026-07-08: M1 was published by no-mistakes as PR #2 and merged. Independent local
  verification reproduced the subagent evidence: ruff clean, mypy clean, 75 tests pass.
  Durable M1 landmines were promoted to AGENTS.md.
- 2026-07-08: WAF clearance farming is isolated in an async Patchright adapter; curl-cffi
  workers consume a proxy/user-agent/cookie handoff, and challenge puzzles are never
  dragged or solved programmatically.
- 2026-07-08: Cookie handoffs persist the curl-cffi impersonation profile (`chrome` by default,
  with `safari_ios` supported), so workers reuse the exact browser identity. The live
  Patchright verification reached AtHome but remained on the security challenge after
  one permitted Click to Verify attempt; before/after captures were retained only under
  ignored `debug/` paths.
- 2026-07-08: Challenge diagnostics (browser trace, WebM, screenshots, JSONL events)
  live exclusively in the operator probe; the production farmer is lean (spec 006) and

  persists only the handoff and session_state. Automated verification is limited to one
  frame-aware semantic press-hold click; puzzle sliders are not dragged or solved
  programmatically.
- 2026-07-08: Page-settling mechanics (tracker-blocking route interception, the
  challenge/listing selector race, settled-content retry, CapSolver solvers) moved to
  `src/athome_harness/scraping/playwright_shared.py` so the probe and the fetcher drive
  identical mechanics; DEBUG-mode route logging hooks in via `set_route_logger`.
  `build_launch_options()` in session_state.py pins one Chrome launch fingerprint
  (viewport, ja-JP locale, Asia/Tokyo tz, UA) for both entry points. Production
  composition is `SessionRefarmer` (HttpDom -> block -> PlaywrightCookieFetcher ->
  session_state.json -> rebound HttpDom); direct adapter use is reserved for unit tests
  and the operator probe.
- 2026-07-08: M7 added the refarm orchestration markers (`REHANDOFF_TRIGGERED`,
  `REHANDOFF_FARMED`, `REHANDOFF_STILL_BLOCKED`, `CURL_HANDOFF_BOUND`,
  `CURL_BLOCK_REHANDOFF`) to the marker contract and proved the direct-first then
  bounded farm/rebind recovery at the real `SessionRefarmer`/`HttpDomAdapter`
  boundary with a fake curl session and fake async farmer. Only the external
  transport and farmer are faked; all orchestration is real code, matching the
  existing unit-test style.

- 2026-09-11: Validated `script#serverApp-state` is the preferred current detail source;
  parse `first-view-ITEMS.propertyData.rentInfo`, validate identity and required fields,
  and fall back to DOM parsing when wrappers or fields change.
- 2026-09-11: Detail SSR retention keeps source-shaped listing, facility, cost, map, nearby-facility, selected agency, and recommendation-card data. `otherPropertyData` cards are immediate `summary_complete` candidates keyed by AtHome `id`, never full details: preserve richer `detail_complete` data, reuse fresh hydrated records, and enqueue canonical detail hydration only when missing or stale. `bukkenNo` is the external identity; `kanriNo` is stored as a non-unique agency property reference. Agencies are separate entities keyed by `kaiinNo`. Rich internal storage remains separate from a compact LLM projection.
- 2026-09-11: Deposit/key-money duration terms remain raw strings with nullable numeric
  yen values; `なし` may map to zero, while `1ヶ月`, `0.5ヶ月`, and `15日` do not.
- 2026-09-11: Building aggregation is post-detail and conservative. It must preserve
  every unit because floor, rent, layout, area, availability, and contract can differ.
- 2026-09-11: OpenCodeGo prompt prefixes must remain static. Session IDs support routing
  but do not guarantee cache hits; dynamic listing data belongs after static instructions.
- 2026-09-11: Live debug captures overwrite fixed ignored files. Challenge bodies remain
  excluded before farm and may be captured only after a valid farmed session with
  explicit `DEBUG=true`; future remote observability requires explicit authorization.
