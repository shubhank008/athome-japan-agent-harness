# Spec 001: AtHome Japan Home Finder (PRD)

> A conversational CLI agent that turns natural-language housing wishes ("pet-friendly
> 1LDK in Osaka under 120k with a real kitchen, near a station") into a ranked shortlist
> of rental and purchase properties from athome.co.jp, with reasons, direct links, and
> memory of what has already been shown, saved, or rejected.

## Context

Manually searching AtHome means: pick prefecture, pick cities, fight dozens of filters
(encoded as opaque `kcXXX` keys), then quick-scan thousands of paginated results
(~300k listings for Osaka rentals alone, VERIFIED: fetch of `/chintai/osaka/list/`
2026-07-08) and open detail pages one by one. Good listings can sit deep in pagination.
This harness automates the funnel: NL query -> AtHome filter params -> broad result
harvest -> LLM shortlist -> detail scrape -> scored recommendations, with persistence so
the agent remembers the user's taste across sessions.

Product trajectory: personal tool now, multi-user later (saved/favorite choices), CLI
now, WebUI later. Architecture decisions below are made to not close those doors.

## Locked decisions (from requirements interview 2026-07-08)

1. On-demand, rate-limited, session-scoped scraping is the default. Webshare proxy
   rotation engages only when the main IP is blocked (USER).
2. The filter map (AtHome filter name -> `kcXXX` code -> human label) is extracted from
   rendered HTML by a standalone tool, versioned in-repo, refreshed by a scheduled
   GitHub Action that auto-files an issue when extraction breaks (USER).
3. Coverage strategy: the live search always scrapes **100% of the LLM-filtered result
   set** (hard filters shrink 300k to a manageable page count). Separately, a durable
   background detail-hydration queue turns rich recommendation cards into a progressively
   warmer local catalogue without extending a live request. This resolves the "golden
   listing hidden on page 6" worry and reduces later detail-fetch latency
   (DESIGN-FRESH, accepted by user).
4. Listing completeness is explicit: search-result observations are `summary_partial`,
   normalized `otherPropertyData` cards are `summary_complete`, and validated canonical
   detail pages are `detail_complete`. Full detail is fresh for 14 days. A positively
   identified unavailable/deleted page deletes its listing, related records, and queued
   job; a block, challenge, timeout, or parser failure is retryable and never deletion
   evidence (USER).
5. Agency profiles are separate deduplicated entities keyed by AtHome `kaiinNo`; detailed
   listing data links to the agency rather than copying its profile into every record
   (USER).
6. Vision-based floor-plan evaluation exists behind an abstract interface, default OFF,
   and will be A/B benchmarked against text-only evaluation (USER).
7. Scraper adapters behind an abstract base: HTTP+HTML->DOM adapter first, Playwright
   adapter as fallback scaffold (USER).
8. Stack: Python 3.12, httpx, selectolax (fallback BeautifulSoup), pydantic v2,
   SQLite via sqlite3, pytest, ruff + mypy (USER).
9. Models: general LLM `deepseek/deepseek-v4-flash-0731`; vision model
   `google/gemma-4-31b-it` (both VERIFIED on OpenRouter 2026-07-08).
10. Webshare: cheapest ($3) plan; proxy/IP rotated per session; Webshare invoked only
   when the main IP is actually blocked (USER).
11. Prefetch scope: Osaka prefecture first, scaling to all prefectures slowly (USER).
12. Multi-value filters: many filters accept a list (e.g., layout "2LDK or 3DK" ->
    `MADORI[]=[...]`). The conditions map (SPEC.md section 1.1) encodes each field's
    cardinality so tool-calling knows the expected parameter signature (USER).
13. Probable Negatives: disabled features in the DOM
    (`p-property__information-facility_disabled-list`, VERIFIED 550 occurrences in the
    Osaka dump) are recorded as Probable Negatives (e.g., "Pets MIGHT not be allowed"),
    scored as caveats, and surfaced in reports (USER).

The project-scoped PRD.md and SPEC.md at the repo root hold the product contract and
the canonical conditions map; this feature spec defers to them for product intent and
filter/field truth.

## User Stories

### US-001: Natural-language rental search
**Description:** As a home seeker, I want to describe what I want in plain language so
that the agent translates it into AtHome filters and searches for me.

**Acceptance Criteria:**
- [ ] A CLI conversational loop accepts a free-text housing query.
- [ ] The agent outputs a concrete `SearchPlan` (rental vs purchase, prefecture, cities,
      hard filter params, soft preference list) and shows it for confirmation before scraping.
- [x] Hard filters from the plan are encoded using the versioned filter map; unknown or
      unmappable constraints are routed to soft preferences, never silently dropped.
- [x] Ambiguous queries trigger a clarifying question instead of a guess.

### US-002: Broad result harvesting
**Description:** As a home seeker, I want the agent to page through all filtered results
so that good listings deep in pagination are not missed.

**Acceptance Criteria:**
- [ ] Given a filter set returning N pages, the harvester fetches all N pages (30
      listings/page, VERIFIED) within the configured rate limit.
- [x] Each listing on a results page is parsed into the listing model (heading block +
      per-unit sub-heading blocks, multiple units per building supported).
- [ ] Progress, page count, and running listing count print to the CLI.
- [x] A polite failure (HTTP 403/429/captcha) pauses, optionally rotates proxy, retries
      with backoff, and aborts gracefully after the retry budget with a partial-results summary.

### US-003: LLM shortlisting
**Description:** As a home seeker, I want the LLM to pick the top X listings from the
broad harvest so detail scraping stays cheap.

**Acceptance Criteria:**
- [x] The full harvest is scored against the query's soft preferences in token-bounded
      batches; scoring is deterministic given the same inputs (temperature 0).
- [x] Output is an ordered shortlist of size X (default 20, DESIGN-FRESH, configurable)
      with a one-line rationale per pick.
- [ ] If the user asks, the top-X list with basic fields is shown before detail scraping.

### US-004: Detail extraction and recommendations
**Description:** As a home seeker, I want the top X listings scraped in full and the best
Y presented with reasons so I can act without opening dozens of tabs.

**Acceptance Criteria:**
- [x] Each shortlisted listing's detail page is scraped into the full detail model: all
      text fields, photo URLs, floor-plan image URL, USP feature tags, and Probable
      Negatives from disabled-feature DOM markers (USER).
- [x] The recommender produces top Y (default 5, DESIGN-FRESH, configurable) with
      per-property reasons mapped to the original query constraints.
- [x] Output is a markdown report AND structured JSON, both containing direct AtHome URLs (USER).
- [x] Every recommendation cites which hard filters and soft preferences it satisfies
      and which it violates, if any.


## DOM Access Map (AtHome HTML contract)

This access map is the maintenance reference for the rental list and detail parsers.
Selectors and labels were verified against the captured Osaka fixtures on 2026-08-18
and the current live `property-card` page on 2026-09-10. The parser supports both the
legacy building/unit DOM and the current card/room DOM. When AtHome changes markup,
update this map and the corresponding fixture/parser tests in the same change. A
challenge page is not a valid fixture and must be rejected before this map is applied.

### Property list page: building and unit access map

| Output field | DOM access path | Scope | Requiredness and parsing rule |
|---|---|---|---|
| Building title | Legacy: `div.p-property--building > h2.p-property__title--building`; current: `div.property-card h2.property-title` | Building | Required for identity context; warn if absent. |
| Address | Legacy hint `dd:nth-of-type(1)`; current: `li.info-item--location` | Building | Optional text; preserve the displayed address. |
| Station | Legacy hint `dd:nth-of-type(2)`; current: `li.info-item--station`, station text matching `「...」駅` | Building | Optional; return `None` when transit text has no station. |
| Walk minutes | Transport hint or current station item, `徒歩N分` | Building | Optional numeric value in minutes. |
| Building type | Legacy hint `dd:nth-of-type(3)`; current: `li.info-item--type` | Building | Extract the type label only, not floor count or construction date. |
| Unit identity | Legacy: `div.p-property__room--detailbox[data-bukken-no]`; current: room link `a[href*='/chintai/']` under `div.room-info-section` | Unit | Required; missing IDs must skip only that unit. |
| Room/floor | Legacy `li.p-property__room-number`; current `li.room-number` | Unit | Optional; detached houses may omit it. |
| Rent | Legacy rent selector; current `.room-info-item.price .rent-value` | Unit | Required for a usable rental summary; bare values are in 万円. |
| Management fee | Legacy price row; current second `.room-info-item__text` in `li.price` | Unit | Optional yen value; absent means zero in the model. |
| Deposit / key money | Legacy key-money row; current `li.fees .room-info-item__text` first/second values | Unit | Preserve `なし`, yen, 万円, and month terms as displayed. |
| Floor plan / area | Legacy room-floorplan selectors; current `li.layout-size` first/second text values | Unit | Optional displayed layout and numeric m² value. |
| USP tags / probable negatives | Legacy facility selectors | Unit | Current cards do not expose equivalent facility lists; leave empty when absent. |
| Photos | `img[src]` or `img[data-original]` under the unit/room block | Unit | Resolve absolute and root-relative URLs; ignore missing images. |
| Detail URL | Unit link or `/chintai/{id}/` fallback | Unit | Use the canonical room ID from the current link path. |
| Building age | Building hint or current type item construction date | Building/Unit | Required by FR-9 when available; never silently default an observed age to `None`. |

### Property detail page: access map

| Output field | DOM access path | Requiredness and parsing rule |
|---|---|---|
| AtHome key | `<title>` numeric `[N]` suffix, with canonical URL as fallback | Required identity; warn and reject if no stable key can be found. |
| Title | Legacy: `table.dataTbl` row whose `<th>` is `建物名・部屋番号`; current: `dl.details` or `table.property-summary__list` with the same label, or `h1` heading | Required display field when present. |
| Address | Legacy: `table.dataTbl` row `<th>` `住所` or `所在地`; current: `dl.details` or `table.property-summary__list` with the same label | Optional text; remove only the `地図で見る` UI suffix. |
| Station and walk minutes | Legacy: `table.dataTbl` row `<th>` `交通`, matching station and `徒歩N分`; current: `dl.details` or `table.property-summary__list` with the same label | Optional independently parsed values. |
| Building type | Legacy: `table.dataTbl` row `<th>` `物件種目` or `種目`; current: `dl.details` or `table.property-summary__list` with the same label | Optional display category. |
| Floor plan | Legacy: `table.dataTbl` row `<th>` `間取り`; current: `dl.details` or `table.property-summary__list` with the same label | Optional layout. |
| Area | Legacy: `table.dataTbl` row `<th>` `専有面積` or `面積`; current: `dl.details` or `table.property-summary__list` with the same label | Optional numeric m² value. |
| Building age | Legacy: `table.dataTbl` row `<th>` `築年月` or `築年数`; current: `dl.details` or `table.property-summary__list` with the same label | Required for FR-9 when exposed; normalize to the model's age representation. |
| Floors | Legacy: `table.dataTbl` row `<th>` `階建 / 階`; current: `dl.details` or `table.property-summary__list` with the same label | Optional raw floor/building text. |
| Description | Legacy: `table.dataTbl` row `<th>` `備考`; current: `dl.details` or `table.property-summary__list` with the same label | Optional free text. |
| Rent | Legacy: `div.paymentInfo.typeChintai dl.data` with `<dt>` containing `賃料`; current: `div.rent-info__item dl.price dd.price__big` | Required for rental details; parse 万円 to yen. |
| Management fee | Legacy: same payment block, `<dt>` containing `管理費`; current: `div.rent-info__item dl.price-cost` `<dt>` `管理費` | Optional yen value. |
| Deposit | Legacy: same payment block, `<dt>` `敷金`; current: `div.rent-info__item dl.price-cost` `<dt>` `敷金` | Optional; preserve month-based terms distinctly from zero. |
| Key money | Legacy: same payment block, `<dt>` `礼金`; current: `div.rent-info__item dl.price-cost` `<dt>` `礼金` | Optional; preserve month-based terms distinctly from zero. |
| Photo URLs | Legacy: `#detail-image_view ul.zoomList li.item img[src|data-original]`; current: `div.swiper-slide__image img` | Optional; resolve absolute and root-relative URLs. |
| Floor-plan image | Same photo item whose `dt#subCategory` is `間取図` | Optional URL and must also be present in `photo_urls` when available. |
| USP tags | `#item-detai_basic__point dd` and `div.pointList ul.typeInline li img[alt]` | Prefer meaningful icon alt labels, otherwise use point text. |
| Facility features | `table.dataTbl` rows with category `<th>` such as `バス・トイレ`, `キッチン`, `収納`, `設備・サービス`, `TV・通信`, `その他` | Enabled items become `facility_features`. |
| Probable negatives | Facility `<td>`, `<p>`, `<span>`, or `<li>` carrying `facility_disabled-list` | Disabled items become `probable_negatives`; this requires a dedicated detail fixture/test. |

### Access-map change checklist

1. Confirm the response is not an AtHome challenge and record the redacted URL and
   challenge marker if it is blocked.
2. Compare the live DOM to the relevant table above, including labels and ancestor
   scope, not just a matching text fragment.
3. Update the parser and its captured/synthetic regression test together.
4. Re-run the parser tests, full gates, and fixture sanity check before changing the
   corresponding row from verified to current.

### US-005: Session memory
**Description:** As a returning user, I want the agent to remember what it already
showed me, what I saved, and what I rejected, so recommendations improve and never repeat.

**Acceptance Criteria:**
- [ ] Every AtHome listing maps to a stable internal property ID (dedupe across
      searches by AtHome listing key; VERIFIED: `BKLISTID` field exists in list HTML).
- [ ] Search history, recommendations, saves, and rejections persist in SQLite.
- [ ] Previously rejected listings are excluded from new shortlists; previously
      recommended-but-unanswered listings are flagged as "seen".
- [ ] The conversational loop accepts feedback commands ("save 2", "reject 1", "more
      like 3") that update the store and can trigger a refined search.

### US-006: Filter map maintenance tool
**Description:** As a maintainer, I want a scheduled tool that re-extracts the filter
map and files an issue when AtHome's DOM changes, so search never silently breaks.

**Acceptance Criteria:**
- [ ] A standalone script fetches reference pages (rental + purchase), extracts every
      filter's name -> code -> label mapping, and writes a versioned JSON snapshot.
- [ ] Extraction validates against a schema (required filters present, codes parse,
      labels non-empty); validation failure opens a GitHub issue with the diff and
      failing selectors (USER).
- [ ] A GitHub Action runs the tool on a weekly schedule and commits the updated map
      when it changes.
- [ ] The harness refuses to encode filters against a map whose schema version it does
      not understand, with a clear error.

### US-007: Purchase-flow search (post-MVP)
**Description:** As a home buyer, I want the same conversational search over purchase
listings (mansion/kodate) so I can use one tool for rent and buy.

**Acceptance Criteria:**
- [ ] The query parser distinguishes rent vs buy intent and selects the correct AtHome flow.
- [ ] The filter map covers purchase-specific filters (price bands, building type).
- [ ] All US-002..US-005 behaviors work identically for purchase listings.

### US-008: Proxy fallback
**Description:** As a user, I want the harness to survive an IP block by rotating
through Webshare proxies so a search does not die mid-run.

**Acceptance Criteria:**
- [ ] A proxy provider interface exists; Webshare is the first implementation, reading
      credentials from environment variables only.
- [ ] On detected block (403/429/captcha signature), including an HTTP 200 AtHome
      puzzle/authentication page, the HTTP adapter emits the challenge/block markers,
      rotates proxy and retries the in-flight request once per proxy, up to the proxy
      retry budget. The challenge page is never parsed or saved as listing data.
- [ ] Rotation and AtHome challenge events are logged with marker lines (see
      contracts/log-markers.md), using redacted URLs only.
- [ ] Direct connection is always tried first; proxies are never used when healthy.

### US-009: Detail hydration cache and agency records (post-MVP)
**Description:** As a home seeker, I want recommendation cards discovered on a detail
page to become a rich local catalogue in the background, so a later live search can
reuse fresh details without waiting for extra detail-page scraping.

**Acceptance Criteria:**
- [ ] A full-detail parse upserts an agency entity keyed by `kaiinNo` and links the
      listing to it without duplicating the agency profile per listing.
- [ ] Every `otherPropertyData` card is normalized and upserted as `summary_complete`;
      it never overwrites a richer `detail_complete` record.
- [ ] A durable, deduplicated FIFO queue schedules canonical detail hydration only when
      the listing lacks fresh detail. Workers atomically claim one job at a time, recheck
      freshness after claim, and skip work already completed by a live request.
- [ ] A successful canonical detail fetch is fresh for 14 days. The live LLM pipeline
      reads a fresh detail record when available, otherwise fetches and upserts detail
      directly; it does not wait for or share worker execution.
- [ ] A positively identified deleted/unavailable page deletes the listing, associated
      listing records, and queued job. Challenge/block/timeout/parser failures are
      retryable failures, not deletion evidence.
- [ ] Background workers use existing rate limits, challenge detection, bounded retries,
      and a circuit breaker that cools down or stops a blocked worker/pool. The feature is
      disabled by default and requires an explicit configuration flag.
- [ ] A future short-lived search-results cache, if needed, is keyed by the complete
      normalized parameter query and remains independent from individual listing cache.

### US-010: Floor-plan evaluation abstraction (post-MVP)
**Description:** As a user, I want pluggable floor-plan/property evaluation so visual
cues (poor condition, old fixtures, bad layout) can improve recommendations later.

**Acceptance Criteria:**
- [ ] `BaseFloorPlanEvaluator` abstract class with `TextDescriptionFloorPlanEvaluator`
      as the default implementation.
- [ ] `VisionFloorPlanEvaluator` stub exists behind the interface, disabled by config.
- [ ] An A/B benchmark harness compares text-only vs vision ranking on a fixed listing
      set and reports agreement/quality deltas (USER).

## Functional Requirements

- FR-1: The CLI must run a conversational loop: query -> shown SearchPlan -> confirm ->
  harvest -> optional top-X preview -> detail scrape -> top-Y report -> feedback commands.
- FR-2: All network access must go through the `BaseScraper` abstraction; business logic
  must not import httpx/playwright directly.
- FR-3: All LLM access must go through a `BaseLLMProvider` abstraction; OpenRouter is
  the first implementation, key read from `OPENROUTER_API_KEY`.
- FR-4: All persistence must go through a `BaseDataStore` abstraction; SQLite is the
  first implementation.
- FR-5: The filter encoder must consume only the versioned filter-map JSON; it must
  raise on unknown filter names or codes rather than guessing.
- FR-6: Rate limiting must be global (per-process), configurable, and default to 1
  request per 2s with jitter (DESIGN-FRESH).
- FR-7: A search run must respect configurable budgets: max pages, max runtime, max
  LLM tokens; hitting any budget must produce a partial report, not a crash (USER: 10D).
- FR-8: Every listing parse must tolerate missing optional fields (e.g., detached houses
  have no room number, USER) and record parse warnings.
- FR-9: The report must include, per recommendation: title, price breakdown (rent +
  management fee + deposit + key money), address, station + walk minutes, floor plan,
  area, building age, USP tags, AtHome URL, reasons, violated constraints.
- FR-10: Secrets (OpenRouter key, Webshare credentials) must come from environment
  variables or `.env` (git-ignored), never hardcoded; `.env.example` must document them.

## Non-Goals

- No WebUI in this milestone (feedback-loop design keeps it possible later).
- No multi-user accounts or auth (single-user local store; schema leaves room).
- No automated contacting of agents/owners, no form submission on AtHome.
- No redistribution/hosting of scraped data; the store is local-only.
- No Playwright implementation beyond an interface-conforming scaffold.
- No vision evaluation enabled by default; benchmark tooling only.
- No purchase-flow search in this milestone (deferred to post-MVP; see US-007).
- No purchase-flow-specific negotiation features (e.g., loan calculators).

## Numeric Values

| Value | Number | Source |
|-------|--------|--------|
| Results per AtHome page | 30 | VERIFIED (user statement + list HTML `ITEMNUM` options 10/20/30) |
| Osaka rental listing volume | ~300k | VERIFIED (user statement; page fetch confirms large corpus) |
| Default shortlist X | 20 | DESIGN-FRESH |
| Default recommendations Y | 5 | DESIGN-FRESH |
| Default rate limit | 1 req / 2s + 0-1s jitter | DESIGN-FRESH (politeness) |
| Max pages per live search | 100 (3,000 listings) | DESIGN-FRESH, configurable |
| Live search runtime budget | 30 min | DESIGN-FRESH, configurable |
| Filter-map refresh cadence | weekly | USER (GitHub Action) |
| Canonical detail freshness TTL | 14 days | USER |
| Background worker execution | FIFO, disabled by default | USER |
| Proxy retry budget | 3 rotations then abort | DESIGN-FRESH |
| HTTP timeout | 30s | DESIGN-FRESH |
| LLM scoring temperature | 0 | DESIGN-FRESH (determinism) |

## Technical Considerations

- AtHome list pages are server-rendered (VERIFIED: 3.3MB HTML for Osaka rentals with
  embedded filter selects). Filter refinement POSTs to
  `https://www.athome.co.jp/chintai/ajax/simplelist/simplelist/` and returns HTML
  fragments (USER, consistent with `simplelist` endpoints found in fetched HTML).
- Filter values use context-dependent `kcXXX` codes: the same code means different
  prices in `PRICEFROM` vs `PRICETO` (USER, VERIFIED in fetched HTML). The filter map
  is therefore keyed by (flow, filter name) and must be extracted per flow.
- `robots.txt` disallows `/*/ajax/` paths for major bots and sets a 10s crawl-delay for
  bingbot (VERIFIED). We honor the spirit via conservative rate limits and session
  scope; we do not honor robots.txt mechanically (user decision, on record).
- Listing identity: `BKLISTID` hidden field observed in list HTML (VERIFIED); the detail
  page URL is the fallback dedupe key.
- LLM cost control: scoring batches carry only fields needed for ranking; detail
  narrative is generated once per shortlisted listing, not per harvest item.

## Success Metrics

- A full search (query -> report) for a typical filtered Osaka rental query completes
  within the runtime budget with zero manual intervention.
- The harness never repeats a rejected listing in a later shortlist.
- Filter-map breakage is detected by the Action within one week of an AtHome DOM change,
  with an issue filed automatically.
- A/B benchmark produces a written report on text-only vs vision evaluation quality.

## Open Questions

1. Which city sets within Osaka qualify for the first prefetch config. Owner: user.
   (Prefecture scope resolved: Osaka first, then scale slowly.)

Resolved 2026-07-08: Webshare = cheapest plan, per-session rotation, invoked only on a
real main-IP block. Models = `deepseek/deepseek-v4-flash-0731` (general) and
`google/gemma-4-31b-it` (vision).
