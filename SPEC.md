# SPEC: AtHome Japan Agent Harness

Project-level technical specification. Source of truth for the AtHome filter/conditions
contract, the data models, and the abstract interfaces. Feature-level task detail lives
in `docs/specs/001-athome-home-finder/`. All field names, codes, and cardinalities below
were VERIFIED against a live dump of `https://www.athome.co.jp/chintai/osaka/list/`
(`dump/osaka_list.html`, captured 2026-07-08).

## 1. AtHome request model

AtHome list refinement is a server-side POST to
`https://www.athome.co.jp/chintai/ajax/simplelist/simplelist/` returning an HTML
fragment. The initial list page is server-rendered HTML containing every filter control,
so the filter map is extracted from that HTML (see section 3).

### 1.1 Conditions map (canonical contract)

Cardinality legend:
- `single`   = one value from an option set (a `<select>`), sent as `FIELD=<code>`
- `multi`    = zero or more values from an option set (checkboxes), sent as repeated
               `FIELD[]=<code>` entries
- `range`    = a from/to pair of `single` selects
- `bool`     = single true/false toggle

| UI label (EN)              | AtHome field      | Cardinality | Code prefix | Notes |
|----------------------------|-------------------|-------------|-------------|-------|
| Rent (from / to)           | PRICEFROM/PRICETO | range       | kc          | context-dependent codes; FROM and TO use different code spaces |
| Rent related               | PRICEOPT[]        | multi       | kc2xx       | e.g. kc201..kc206 |
| Floor plan (layout)        | MADORI[]          | multi       | km          | 13 options; "2LDK or 3DK" -> multiple codes |
| Minimum area               | MENSEKI           | single      | kt          | 18 options (m2 thresholds) |
| Walking distance to station| EKITOHO           | single      | ke          | 8 options (minute thresholds) |
| Year built / building age  | CHIKUNENSU        | single      | kn          | 11 options |
| Surrounding environment    | SYUHENKANKYO[]    | multi       | kw          | 5 options |
| Property type              | SHUMOKU[]         | multi       | kb          | 3 options |
| Building structure         | TATEKOUZOU[]      | multi       | kh          | 4 options |
| Contract terms             | KEIYAKU           | single      | ki          | 3 options (fixed-term lease handling) |
| Home renovation/remodel    | (reno toggle)     | bool        | -           | single true/false |
| Information release date   | JOHOKOKAI         | single      | -           | listing age |
| Appeal (has recommendation)| (appeal toggle)   | bool        | -           | properties with recommendation comments |
| Image                      | GAZO[]            | multi       | kg          | 4 options |
| Specific Requirements      | KODAWARI[]        | multi       | K\d+        | 103 options, grouped by category (section 1.2) |
| Sort order                 | SORT              | single      | numeric     | 10 options; 33 = newest first |
| Results per page           | ITEMNUM           | single      | numeric     | 10/20/30 |

Other request fields (context, not user filters): `KEN_CD` (prefecture), `SHIKU_CD`/
`CHOSON` (cities), `ENSEN_CD`/`EKI_CD` (line/station), `PAGENO`, `BKLISTID` (listing
identity), geo bounds `LAT/LON/LATMIN/...`.

### 1.2 KODAWARI[] categories (Specific Requirements)

The 103 `KODAWARI[]` options group into these categories (labels and live listing counts
verified from the dump). Each is a single checkbox within the multi-value `KODAWARI[]`
array; selecting several across categories is allowed.

- Kitchen: system kitchen, counter kitchen, IH heater, gas stove, 2+ burner stove,
  water purifier, dishwasher/dryer, garbage disposal
- Bathroom & toilet: separate bath/toilet, same-room, reheating, bathroom dryer,
  bathroom heating, bathroom TV, mist sauna, shower vanity, separate washroom, heated
  toilet seat, tankless toilet
- Heating & cooling: air conditioner, all-room AC, underfloor heating
- Storage: walk-in closet, storage space, underfloor storage, storage units, shoe box
- TV & comms: BS, CS, CATV, optical fiber, free internet
- Security: auto-lock, monitor intercom, delivery box, 24h security, dimple key,
  electric shutter, security cameras, security glass
- Position: 2nd floor+, top floor, 1st floor, corner room
- Conditions: immediate occupancy, two-person, women-only, pet consultation, large-dog,
  small-dog, cat, musical instrument, office-ok, free rent, two-family, anytime garbage,
  DIY-ok, no guarantor, etc.
- Shared facilities: elevator, resident manager, front desk, fitness, parking available
- Facilities & features: flooring, all-hardwood, indoor washer space, washer space,
  washer+dryer, city gas, propane, double-glazed, 24h ventilation, energy-saving water
  heater, indoor drying rack, EV charging
- Features: south-facing, quiet residential, condominium type, barrier-free, all-electric,
  furnished, appliances included, bay window, maisonette, loft, designer, tile exterior,
  smart home
- Construction: non-formaldehyde, double floor/ceiling
- Others: balcony, roof balcony, multi-side balcony, wooden deck, garden, parking
  (incl. nearby), 2-car parking, visitor parking, bicycle parking, motorcycle parking

### 1.3 Probable Negatives (disabled features)

On a listing, an offered-but-disabled feature renders as
`<li class="p-property__information-facility_disabled-list">Pet consultation</li>`.
This is a first-class signal, verified 550 occurrences in the Osaka dump. The parser
must record these as **Probable Negatives**: the feature is plausibly absent or
unavailable (e.g., "Pets MIGHT not be allowed", "Pet consultation not available"), not
merely unlisted. They feed soft-preference scoring and must appear in the report as
caveats, distinct from confirmed USP features.

## 2. Filter map (versioned)

The mapping of (flow, field) -> [{code, label}] is extracted from rendered HTML by
`tools/dump_filter_map.py` and stored as versioned JSON (`filters/data/filter_map.v1.json`).

- Keyed by (flow, field) because codes collide across fields and flows.
- Schema-validated: required fields present, codes match their prefix, labels non-empty,
  price options monotonic.
- A weekly GitHub Action re-extracts and either commits the updated map or files a
  GitHub issue with the failing selectors on schema/validation failure.
- The encoder consumes only this map and raises `UnknownFilter` / `UnknownFilterValue`
  rather than guessing. The harness refuses a map whose schema version it does not know.

## 3. Data models (pydantic)

Field-by-field reference with defaults and types: `docs/reference/data-models.md`.
The list-page `photo_urls` are the inline thumbnails only; the detail page overrides
them with the full gallery set and adds `floor_plan_image_url` (see the photo-coverage
note in the reference).

- `SearchPlan`: flow, prefecture, cities[], hard_filters (typed per section 1.1
  cardinality), soft_prefs[], plus budgets.
- `ListingSummary`: one per unit (multi-unit buildings yield several summaries sharing a
  building identity). Fields: internal_id, athome_key (BKLISTID), url, title, address,
  station + walk_minutes, building_type, floors, age, rent, management_fee, deposit,
  deposit_raw, key_money, key_money_raw, floor_plan, area_m2, usp_tags[],
  probable_negatives[], photo_urls[]. Raw deposit/key-money terms (e.g. ``1ヶ月``) are
  preserved alongside yen values so month-based terms are never indistinguishable from
  zero.
- `ListingDetail`: ListingSummary + full text fields, all photo_urls, floor_plan_image_url,
  facility_features[].
- `Recommendation`: listing ref, rank, reasons[], satisfied_constraints[],
  violated_constraints[], probable_negatives[].
- `FilterMap`: versioned (flow, field) -> options, with content hash.
- `RunReport`: query, plan, counts, shortlist, recommendations, budgets consumed,
  partial flag.

### 3.1 Current detail payload and hydration contract

AtHome detail pages currently expose a structured SSR payload in
`script#serverApp-state` with `type="application/json"`. The preferred extraction
path is `first-view-ITEMS.propertyData.rentInfo`; it is validated against the
requested listing ID and required identity/price fields before use. When the
script, wrapper, or required fields are missing, detail parsing falls back to the
current DOM selectors. The payload schema and sanitized example live in
`docs/reference/server-app-state.md`.

`ListingSummary` values from the broad result page are the hydration base. A
successful detail parse overrides only observed detail fields. A failed or
incomplete detail parse preserves the summary values and sets
`ListingDetail.listing_detail=false` plus an operator-safe
`detail_failure_reason`. Duration terms such as `1ヶ月` remain raw strings and
are not coerced into yen.

### 3.2 Current detail fields

The detail contract includes contract period, building name, building structure,
total units, construction date/raw age/display age, remarks, facility features,
PICK UP feature labels, probable negatives, price terms, transport, unit floor,
full images, and floor-plan image. Current DOM selectors and the structured-state
mapping must be updated together with fixtures and regression tests when AtHome
changes markup.


## 4. Abstract interfaces (Abstract First)

- `BaseScraper`: fetch_html / fetch_binary; raises `BlockDetected` (signature: 403/429/
  captcha). Adapters: `HttpDomAdapter` (curl-cffi + selectolax), `PlaywrightAdapter`
  (scaffold). `PlaywrightCookieFetcher` (async Playwright browser farmer) produces a
  `CookieHandoff` consumed by `HttpDomAdapter`. `SessionRefarmer` orchestrates the
  production fallback loop (HttpDom -> block -> PlaywrightCookieFetcher -> handoff ->
  HttpDom rebound). No third-party HTTP import outside adapters.
- `BaseLLMProvider`: complete_json(schema) with token accounting. Shared
  `OpenAICompatibleProvider` base over curl-cffi; concrete transports: OpenRouter
  (`openrouter.py`) and OpencodeGo (`opencodego.py`). Provider selection is
  config-driven via `ATHOME_LLM_PROVIDER`. General model
  `deepseek/deepseek-v4-flash-0731`, vision `google/gemma-4-31b-it`.
- `BaseDataStore`: upsert_listing, record_search, record_recommendation, save, reject,
  history queries. SQLite impl; listings keyed by internal id with BKLISTID + URL dedupe.
- `BaseFloorPlanEvaluator`: evaluate(detail) -> score/notes. `TextDescription...`
  default; `Vision...` stub behind config, off by default.
- `ProxyProvider`: Webshare impl; rotation per session, engaged only on block, direct
  first, retry budget 3.

## 5. Budgets and limits (defaults, configurable)

| Knob | Default | Source |
|------|---------|--------|
| Rate limit | 1 req / 2s + 0-1s jitter | DESIGN-FRESH |
| Results/page | 30 | VERIFIED |
| Shortlist X | 20 | DESIGN-FRESH |
| Recommendations Y | 5 | DESIGN-FRESH |
| Max pages / live search | 100 | DESIGN-FRESH |
| Runtime budget | 30 min | DESIGN-FRESH |
| HTTP timeout | 30s | DESIGN-FRESH |
| Proxy retries | 3 | DESIGN-FRESH |
| Prefetch cache TTL | 48h | DESIGN-FRESH |
| LLM scoring temperature | 0 | DESIGN-FRESH |

## 5.1 Next implementation phases

The following work is intentionally granular and should be delivered as one focused
commit per task.

### Diagnostics and observability

- Add stable DEBUG overwrite artifacts for last successful list/detail HTML, last
  post-handoff challenge HTML, last failed request metadata, and raw LLM inputs,
  outputs, repairs, and invalid responses.
- Add per-target detail failure metadata with redacted URL, listing ID, stage,
  exception type, and meaningful message.
- Keep direct challenge bodies excluded by default. A post-farmed challenge capture
  is allowed only under explicit `DEBUG=true` and ignored local paths.
- Future roadmap: dated debug directories with 14-day cleanup, log rotation, and
  an opt-in remote analysis sink requiring explicit authorization.

### Data contract and aggregation

- Represent deposit and key-money durations as raw terms with nullable numeric yen
  fields; only direct yen/万円 terms may populate numeric values.
- Preserve construction date/raw age and expose rounded numeric and human-readable age.
- Prefer validated `serverApp-state` detail data and fall back to DOM parsing.
- Add a conservative post-detail `Building` aggregate containing shared metadata and
  a list of unit entities preserving floor, rent, area, contract, and availability.
- Never deduplicate units by title alone; retain every unit for saves, rejects, and URLs.

### Geography and filters

- Route list URLs from parsed flow and prefecture rather than hard-coded Osaka.
- Resolve parsed city/area names to verified AtHome slugs and encode geography in
  requests when an exact mapping exists.
- Verify authorized rental and purchase smoke paths for Osaka, Tokyo, and Sapporo.
- Include the complete parsed query plan, hard filters, soft preferences, and encoded
  parameter summary in persisted JSON reports.

### Performance and LLM quality

- Use monotonic clocks for every production stage and request duration marker.
- Keep current detail selectors in the browser settle race and measure timeout rates.
- Keep OpenCodeGo static system/schema prompt prefixes stable; put dynamic listing
  data after the prefix and never put timestamps or UUIDs in cacheable prefixes.
- Measure prompt caching from provider evidence; a stable session ID alone does not
  guarantee cache hits.
- Evaluate compact shortlist and recommender projections against the full model,
  preserving fields required for ranking quality.
- Constrain recommendation reasons and constraint arrays; add a recommendation
  output budget distinct from the per-request timeout.
- Benchmark bounded shortlist concurrency, provider throttling, repair frequency,
  latency, and total wall time before raising worker counts.

### Test and maintenance quality

- Prevent tests from loading an operator `.env` by default; pass `_env_file=None`
  in settings test factories and preserve deterministic provider defaults.
- Keep validated current list/detail captures, selector maps, and parser tests in
  one change whenever AtHome markup changes.
- Require full lint, type, unit, e2e fixture, and documentation gates before
  publication.

## 6. Configuration

All runtime config via env / `.env` (git-ignored); `.env.example` documents every key
and stays in sync with the parser (repo invariant). Keys: `OPENROUTER_API_KEY`,
`OPENCODEGO_API_KEY`, `WEBSHARE_PROXY_USER`, `WEBSHARE_PROXY_PASS`,
`ATHOME_LLM_PROVIDER`, `ATHOME_STORE_PROVIDER`, `ATHOME_SCRAPER_PROVIDER`,
`ATHOME_OPENCODEGO_MODEL`, `ATHOME_OPENCODEGO_BASE_URL`, `ATHOME_STORE_PATH`,
model names, and the budget knobs above. Complete key-by-key reference with
types, defaults, and env aliases: `docs/reference/config.md`.
