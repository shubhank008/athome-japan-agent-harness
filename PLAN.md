# PLAN

Live project plan. Updated after every feature or update, per AGENTS.md.

## Current state

M0 (project skeleton + hygiene), M1 (scraper core), M2 (filter map), and M3 (parsing)
implemented and verified. M0: `config.py` (strict env parser + `Budgets`), `models.py`
(pydantic data models), `pyproject.toml` + exact-pinned `requirements.txt`. M1:
`scraping/base.py` (`BaseScraper`, `BlockDetected`, `ProxyProvider`),
`scraping/rate_limiter.py` (token-bucket with jitter), `scraping/http_adapter.py`
(curl-cffi + selectolax DOM adapter with block detection, proxy rotation, and AtHome
challenge detection), `scraping/playwright_adapter.py` (scaffold),
`scraping/proxy/base.py` + `scraping/proxy/webshare.py` (proxy rotation policy). M2:
`filters/map_schema.py` (versioned schema + validation with missing-flow rejection),
`filters/encoder.py` (SearchPlan -> POST params), `tools/dump_filter_map.py`
(extraction tool), `.github/workflows/filter-map.yml` (weekly refresh), checked-in
`filters/data/filter_map.v1.json`. M3: `scraping/list_parser.py` (results HTML ->
`ListingSummary` list), `scraping/detail_parser.py` (detail HTML -> `ListingDetail`),
live-captured fixtures in `tests/fixtures/`. M3 parser hardening remains pending for
building age, normalized building type, month-based deposit terms, detail disabled-feature
coverage, and the required second/third detail fixtures. Feature 002 adds the Patchright
cookie farmer and typed curl-cffi handoff; Feature 006 adds lean production refarming.
Merged `origin/main` currently passes 190 unit tests, ruff, and mypy. No LLM, store, or
orchestration yet.

## Active feature

| Feature | Spec | Status |
|---------|------|--------|
| 001 AtHome Home Finder | `docs/specs/001-athome-home-finder/` (spec, plan, marker contract) | M0, M1, M2 done; M3 merged with hardening follow-up |
| 002 Playwright Cookie Fetcher | `docs/specs/002-playwright-cookie-fetcher/` | merged through PR #7; security boundary and live behavior require ongoing review |
| 003 curl-cffi HTTP Integration | `docs/specs/003-curl-cffi-http-integration/` | merged through PR #7; bounded refarm path implemented |
| 004 Playwright Challenge Diagnostics | `docs/specs/004-playwright-challenge-diagnostics/` | merged through PR #7; operator diagnostics path implemented |
| 005 Patchright Runtime | `docs/specs/005-patchright-runtime/` | merged through PR #7; live challenge behavior remains operationally constrained |
| 006 Lean Cookie Fetcher | `docs/specs/006-lean-cookie-fetcher/` | merged through PR #7; production diagnostics reduced to handoff/session state |

## Feature 001 summary

Conversational CLI that turns natural-language housing wishes into ranked rental and
purchase recommendations from athome.co.jp. Funnel: NL query -> SearchPlan -> AtHome
filter encoding (versioned filter map) -> full harvest of filtered results -> LLM
shortlist (top X) -> detail scrape -> top-Y report (markdown + JSON) -> persistent
memory (seen/saved/rejected). Abstract-first: BaseScraper (curl-cffi adapter now,
Playwright scaffold), PlaywrightCookieFetcher (async browser farmer producing a
typed CookieHandoff), BaseLLMProvider (OpenRouter first), BaseDataStore (SQLite first),
BaseFloorPlanEvaluator (text default, vision stub). Webshare proxy rotation on block
detection only. Weekly GitHub Action re-extracts the filter map and files an issue on
DOM drift. Post-MVP: prefetch cache with freshness ordering and dead-listing
revalidation, vision A/B benchmarks.

## Milestone board (001)

| Milestone | Tasks | State |
|-----------|-------|-------|
| M0 Skeleton | T01-T04 | done (2026-07-08, `feat/001-m0-skeleton`, verified) |
| M1 Scraper core | T05-T09 | done (2026-07-08, `feat/001-m1-scraper`, PR #2 merged, independently verified) |
| M2 Filter map | T10-T13 | done (2026-08-17, `feat/001-m2-filter-map`) |
| M3 Parsing | T14-T16 | merged through PR #4; hardening follow-up pending |
| M4 LLM layer | T17-T21 | todo |
| M5 Store | T22-T23 | todo |
| M6 Orchestration + CLI | T24-T26 | todo |
| M7 Maintenance surfaces | T27-T28 | todo |
| M8 Post-MVP | T29-T31 | spec'd, not scheduled |

## Decisions log

- 2026-07-08: Live searches scrape 100% of the LLM-filtered result set; broad-net
  coverage for unfiltered exploration is delegated to the optional prefetch cache
  (freshness-sorted), not to live searches. Rationale: 300k-listing prefectures make
  percentage-of-everything live scraping multi-hour and rate-limit hostile.
- 2026-07-08: robots.txt is honored in spirit (rate limits, session scope) not
  mechanically; user decision, on record.
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
