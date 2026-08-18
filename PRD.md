# PRD: AtHome Japan Agent Harness

Project-level product requirements. Feature-level detail lives in
`docs/specs/001-athome-home-finder/`. This file is the stable, project-scoped contract;
the feature spec is the working, task-scoped one. When they disagree, this file wins
for product intent and the feature spec wins for implementation detail.

## Product vision

A conversational agent that finds homes to rent or buy on athome.co.jp from natural
language. You describe what you want ("pet-friendly 2LDK or 3DK in Osaka under 120k,
real kitchen, near a station"); the agent translates that into AtHome's filter system,
harvests the matching listings, shortlists with an LLM, scrapes the best in detail, and
returns a ranked, reasoned shortlist with direct links. It remembers what you have
seen, saved, and rejected so results improve over time and never repeat.

## Problem

Manually searching AtHome is tedious:
- Pick prefecture, then cities, then fight dozens of filters encoded as opaque `kcXXX`
  keys that differ per field and per flow.
- A single Osaka rental query returns ~300k listings across thousands of pages; good
  listings hide deep in pagination.
- Comparing candidates means opening many detail pages and reading floor plans and
  condition photos one by one.

## Target users

- Now: a single user (the author) searching for a home in Japan.
- Later: multiple users with saved/favorite choices, and a WebUI. Architecture must not
  close these doors, but neither is built in this milestone.

## Core value proposition

Turn a vague housing wish into a small, trustworthy, reasoned shortlist, at a fraction
of the manual effort, without missing listings buried in pagination.

## How it works (the funnel)

1. Parse natural language into a `SearchPlan`: flow (rent/buy), prefecture, cities,
   hard filters (mapped to AtHome params), and soft preferences (LLM-scored later).
2. Encode hard filters through a versioned, context-keyed filter map. Unmappable
   constraints become soft preferences, never silently dropped.
3. Harvest 100% of the filtered result set (30 listings/page) within polite rate limits.
4. LLM-scores the harvest against soft preferences, keeps top X (default 20).
5. Scrapes the top X detail pages in full (all fields, photos, floor-plan image, USP
   tags, and Probable Negatives).
6. Recommends top Y (default 5) with reasons and violated constraints, as markdown + JSON.
7. Accepts feedback (save / reject / more-like) that persists and refines future runs.

## Scope

### In scope (this milestone)
- Conversational CLI (terminal) for rental search, all prefectures, Osaka as the focus.
- Abstract-first layers: `BaseScraper` (curl-cffi adapter now, Playwright scaffold),
  `PlaywrightCookieFetcher` (async browser farmer for session handoff),
  `BaseLLMProvider` (OpenRouter first), `BaseDataStore` (SQLite first),
  `BaseFloorPlanEvaluator` (text default, vision stub).
- Versioned filter map + a weekly GitHub Action that re-extracts it and files an issue
  on DOM drift.
- Webshare proxy rotation, engaged only when the main IP is blocked.
- Session memory (seen / saved / rejected) in SQLite.
- Rate limits and per-run budgets (pages, runtime, tokens) with graceful partial results.

### Post-MVP (spec'd, scheduled later)
- Purchase flow (mansion/kodate) with the same funnel.
- Optional background prefetch cache, freshness-sorted, starting with Osaka prefecture
  and scaling to all prefectures slowly; background revalidation marks dead listings.
- Vision floor-plan evaluation A/B benchmarked against text-only.

### Out of scope
- WebUI, multi-user accounts/auth (this milestone).
- Contacting agents/owners or submitting any AtHome form.
- Redistributing or hosting scraped data; the store is local-only.
- Enabling vision evaluation by default.

## Success criteria

- A typical filtered Osaka rental search completes end-to-end within the runtime budget
  with no manual steps.
- A rejected listing never reappears in a later shortlist.
- Filter-map breakage is detected by the weekly Action within 7 days of an AtHome DOM
  change, with an issue auto-filed.
- The vision A/B benchmark produces a written quality comparison.

## Key constraints and decisions

- On-demand, rate-limited, session-scoped scraping is the default; bulk prefetch is
  opt-in only.
- Filter codes (`kcXXX`, `ktXXX`, etc.) are context-dependent; the filter map is keyed
  by (flow, field) and versioned.
- Many filters are multi-value (e.g., layout "2LDK or 3DK" -> `MADORI[]=[...]`); the
  conditions map encodes cardinality so tool-calling knows each parameter's signature.
- Disabled features in the DOM (`p-property__information-facility_disabled-list`) are
  recorded as Probable Negatives (e.g., "Pets MIGHT not be allowed"), not ignored.
- robots.txt is honored in spirit (rate limits, session scope), not mechanically.
- Models: general LLM `deepseek/deepseek-v4-flash-0731`; vision `google/gemma-4-31b-it`
  (both verified on OpenRouter 2026-07-08).
- Secrets (OpenRouter key, Webshare credentials) come from env / `.env` only.

## Open questions

- Exact Webshare plan limits and rotation semantics (sticky-session vs per-request).
  Owner: user. Blocks only proxy implementation detail.
- Which city sets within Osaka qualify for the first prefetch config. Owner: user.
