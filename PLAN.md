# PLAN

Live project plan. Updated after every feature or update, per AGENTS.md.

## Current state

Project is pre-implementation. No source code yet.

## Active feature

| Feature | Spec | Status |
|---------|------|--------|
| 001 AtHome Home Finder | `docs/specs/001-athome-home-finder/` (spec, plan, marker contract) | Spec approved in conversation; implementation not started |

## Feature 001 summary

Conversational CLI that turns natural-language housing wishes into ranked rental and
purchase recommendations from athome.co.jp. Funnel: NL query -> SearchPlan -> AtHome
filter encoding (versioned filter map) -> full harvest of filtered results -> LLM
shortlist (top X) -> detail scrape -> top-Y report (markdown + JSON) -> persistent
memory (seen/saved/rejected). Abstract-first: BaseScraper (HTTP adapter now, Playwright
scaffold), BaseLLMProvider (OpenRouter first), BaseDataStore (SQLite first),
BaseFloorPlanEvaluator (text default, vision stub). Webshare proxy rotation on block
detection only. Weekly GitHub Action re-extracts the filter map and files an issue on
DOM drift. Post-MVP: prefetch cache with freshness ordering and dead-listing
revalidation, vision A/B benchmarks.

## Milestone board (001)

| Milestone | Tasks | State |
|-----------|-------|-------|
| M0 Skeleton | T01-T04 | todo |
| M1 Scraper core | T05-T09 | todo |
| M2 Filter map | T10-T13 | todo |
| M3 Parsing | T14-T16 | todo |
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
