# Data models

The pydantic v2 contract for the whole harness, defined in
`src/athome_harness/models.py`. These models are the single source of truth for
what a listing, a plan, and a report look like; every parser, LLM stage, and the
store produce or consume them. SPEC.md section 3 is the product-level summary;
this page is the field-by-field reference.

* **Depends on:** `config.Budgets` (embedded in `SearchPlan` and `RunReport`).
* **Depended on by:** every parser, every LLM stage, the harvester, the store,
  the recommender, and the report renderer.

All models are immutable-by-convention pydantic `BaseModel`s. Note the
repository landmine: `model_copy(update=...)` does **not** re-run validators, so
tests that need an invalid instance must build it via `Model.model_validate({...})`
or direct construction, never by copying a valid one.

## PriceBreakdown

Monetary breakdown for one unit. Rent and management fee are always integers in
yen. Deposit and key money are nullable: when the term is directly convertible to
yen (e.g. `なし` maps to `0`), the integer field is populated; when the term is a
duration (e.g. `1ヶ月`), the integer field is `None` and the raw text is
preserved so a month-based term is never indistinguishable from zero.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `rent` | `int` (>= 0) | required | Monthly rent in yen. |
| `management_fee` | `int` (>= 0) | `0` | Monthly management fee in yen. |
| `deposit` | `int \| None` (>= 0) | `None` | Upfront deposit in yen when directly numeric; duration terms remain `None`. |
| `key_money` | `int \| None` (>= 0) | `None` | Upfront key money in yen when directly numeric; duration terms remain `None`. |
| `deposit_raw` | `str \| None` | `None` | Raw deposit term (for example `1ヶ月`) when it is not a plain yen value. |
| `key_money_raw` | `str \| None` | `None` | Raw key-money term when it is not a plain yen value. |

The `*_raw` fields exist because AtHome expresses some deposits and key money as
a count of months (`1ヶ月`) rather than a yen figure. Duration terms leave the
numeric field as `None` and are recorded in the raw field so they are never
mistaken for "no deposit". Converting a month term to yen requires the unit's
rent, which the parser does not assume.

## ListingCompleteness

StrEnum tracking the highest observed data completeness for a listing record.

| Value | Meaning |
|-------|---------|
| `summary_partial` | Observed from a broad search-result page; limited fields. |
| `summary_complete` | Normalized from a recommendation card (`otherPropertyData`). |
| `detail_complete` | Hydrated from a canonical detail page; fresh for 14 days. |

## Agency

AtHome agency record, deduplicated by `kaiinNo`. Separate entity linked from
listings rather than copied into each record.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `kaiin_no` | `str` | required | AtHome agency member number (`kaiinNo`). |
| `kaiin_link_no` | `str \| None` | `None` | AtHome agency link number. |
| `name` | `str \| None` | `None` | Agency display name. |
| `postal_code` | `str \| None` | `None` | Agency postal code. |
| `address` | `str \| None` | `None` | Agency address. |
| `phone` | `str \| None` | `None` | Agency telephone or fax text. |
| `url` | `str \| None` | `None` | Agency detail URL, when supplied. |
| `representative` | `str \| None` | `None` | Agency representative name. |
| `domain` | `str \| None` | `None` | Agency web domain. |
| `access` | `str \| None` | `None` | Agency station access text. |
| `business_hours` | `str \| None` | `None` | Agency operating hours. |
| `holidays` | `str \| None` | `None` | Agency regular holidays. |
| `features` | `str \| None` | `None` | Raw agency feature text. |
| `associations` | `str \| None` | `None` | Raw association membership text. |
| `license_number` | `str \| None` | `None` | Agency license text. |
| `raw` | `dict[str, object]` | `{}` | Unmapped raw `kaiinInfo` values. |

## ImageRecord

One structured detail image, retaining source metadata.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `url` | `str` | required | Source image URL or path. |
| `title` | `str \| None` | `None` | Image title or caption. |
| `category` | `str \| None` | `None` | Image sub-category. |
| `raw` | `dict[str, object]` | `{}` | Unmapped source payload. |

## AccessRecord

One structured transit/access option.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `line_name` | `str \| None` | `None` | Railway or bus line name. |
| `station_name` | `str \| None` | `None` | Station name. |
| `walk_time` | `str \| None` | `None` | Walking time or access text. |
| `raw` | `dict[str, object]` | `{}` | Unmapped source payload. |

## FacilityRecord

One nearby facility with distance and source metadata.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `title` | `str` | required | Facility name. |
| `category` | `str \| None` | `None` | Facility category label. |
| `distance` | `str \| None` | `None` | Distance text. |
| `image_url` | `str \| None` | `None` | Facility image URL. |
| `raw` | `dict[str, object]` | `{}` | Unmapped source payload. |

## FeatureGroup

One categorized facility feature group.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `title` | `str` | required | Feature group title. |
| `text` | `str` | required | Feature group text. |
| `raw` | `dict[str, object]` | `{}` | Unmapped source payload. |

## StructuredDetail

Rich server-state data retained separately from the LLM projection. Persisted
through the store and linked from `ListingDetail` but never sent to the LLM.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `raw` | `dict[str, object]` | `{}` | Full parsed rentInfo payload. |
| `romanized` | `dict[str, str]` | `{}` | Romanized field values from the SSR state. |
| `access` | `list[AccessRecord]` | `[]` | Transit and access records. |
| `images` | `list[ImageRecord]` | `[]` | Full detail image gallery. |
| `nearby_facilities` | `list[FacilityRecord]` | `[]` | Nearby facility records. |
| `feature_groups` | `list[FeatureGroup]` | `[]` | Categorized facility features. |
| `surrounding_info` | `dict[str, object]` | `{}` | Surrounding area data. |
| `cost_info` | `dict[str, object]` | `{}` | Cost and fee data. |
| `appeal_point` | `str \| None` | `None` | Free-text appeal point. |
| `building_info` | `dict[str, object]` | `{}` | Building metadata. |
| `other_property_info` | `dict[str, object]` | `{}` | Other property information. |

## RecommendationCard

Typed recommendation card retained alongside its normalized summary. Cards are
normalized into `ListingSummary` records via `recommendation_cards.py` and are
never treated as full detail.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `athome_key` | `str` | required | AtHome recommendation card identifier. |
| `title` | `str` | `""` | Displayed recommendation title. |
| `seo_path` | `str` | `"chintai"` | AtHome URL path segment. |
| `location` | `str` | `""` | Displayed location and transit text. |
| `raw` | `dict[str, object]` | `{}` | Complete source card payload. |

## HydrationIntent

Non-durable in-memory request to hydrate a normalized recommendation card
into a full detail record later. Returned by `ingest_recommendation_cards`;
not persisted by the queue (T32 owns durable jobs).

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `athome_key` | `str` | required | AtHome listing identifier to hydrate. |
| `internal_id` | `str` | required | Normalized listing identity. |
| `url` | `str` | required | Canonical detail URL. |

## HydrationStatus

StrEnum for the durable lifecycle states of one detail-hydration job.

| Value | Meaning |
|-------|---------|
| `queued` | Eligible for claim; no active lease. |
| `leased` | Claimed by a worker; lease active. |
| `succeeded` | Detail was hydrated and acknowledged. |
| `skipped` | Claim-time freshness suppressed work. |
| `failed` | Terminal failure after exhausting attempts. |
| `cancelled` | Explicitly cancelled (positive unavailable detection). |

## HydrationJob

One durable FIFO detail-hydration job and its retry state.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `job_id` | `int` | required | Auto-incrementing row ID. |
| `athome_key` | `str` | required | AtHome listing identifier. |
| `url` | `str` | required | Canonical detail URL. |
| `internal_id` | `str` | required | Normalized listing identity. |
| `status` | `HydrationStatus` | required | Current lifecycle state. |
| `attempts` | `int` (>= 0) | required | Number of claim attempts. |
| `max_attempts` | `int` (>= 1) | required | Attempt ceiling before terminal failure. |
| `created_at` | `datetime` | required | Job creation timestamp. |
| `updated_at` | `datetime` | required | Last status update timestamp. |
| `lease_token` | `str \| None` | `None` | Active lease UUID, when claimed. |
| `lease_until` | `datetime \| None` | `None` | Lease expiry timestamp. |
| `last_error` | `str \| None` | `None` | Last error detail text. |
| `last_error_category` | `str \| None` | `None` | Failure category (e.g. `transient`, `parser`, `block`). |

## ListingSummary

One unit of a building. A multi-unit building yields several summaries that
share a building identity but differ per unit. Produced by
[`parse_list_page`](parsers.md).

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `internal_id` | `str` | required | Stable internal property ID used for dedupe. |
| `completeness` | `ListingCompleteness` | `summary_partial` | Highest observed listing data completeness level. |
| `detail_fetched_at` | `datetime \| None` | `None` | UTC timestamp when detail data was fetched. |
| `detail_fresh_until` | `datetime \| None` | `None` | UTC timestamp through which fetched detail is fresh. |
| `agency` | `Agency \| None` | `None` | Persisted listing agency, when known. |
| `agency_reference` | `str \| None` | `None` | Partial agency reference from a summary source. |
| `athome_key` | `str` | required | AtHome `BKLISTID` listing key. |
| `url` | `str` | required | Canonical AtHome listing URL. |
| `title` | `str` | required | Human-readable listing title. |
| `address` | `str` | required | Street/presented address of the unit. |
| `station` | `str \| None` | `None` | Nearest station name, when known. |
| `walk_minutes` | `float \| None` | `None` | Walking minutes to the station. |
| `building_type` | `str \| None` | `None` | Building category label. |
| `floors` | `str \| None` | `None` | Floor/build-height descriptor, raw text. |
| `age` | `float \| None` | `None` | Rounded building age in years, when exposed. |
| `age_raw` | `str \| None` | `None` | Raw displayed age or construction term. |
| `construction_date` | `str \| None` | `None` | Raw construction date when exposed. |
| `age_display` | `str \| None` | `None` | Human-readable age, such as `1 month old`. |
| `price` | `PriceBreakdown` | required | Monetary breakdown for the unit. |
| `floor_plan` | `str \| None` | `None` | Layout descriptor (for example `1LDK`). |
| `area_m2` | `float` | required | Floor area in square metres. |
| `usp_tags` | `list[str]` | `[]` | Confirmed feature highlights (enabled facilities). |
| `probable_negatives` | `list[str]` | `[]` | Disabled features surfaced as caveats. |
| `photo_urls` | `list[str]` | `[]` | Photo URLs known at this stage (see note below). |
| `source_data` | `dict[str, object]` | `{}` | Raw source payload retained for provenance. |

A validator enforces that `detail_fetched_at` and `detail_fresh_until` are
both present or both absent, and that `fresh_until` does not precede
`fetched_at`.

### Photo coverage: summary vs detail

This is the field that most often causes confusion, so it is called out
explicitly.

* On the **list page**, `photo_urls` holds the handful of thumbnail images
  shown inline per unit (about 6 in the captured Osaka fixture).
* On the **detail page**, [`parse_detail_page`](parsers.md) re-parses the full
  gallery and **overrides** `photo_urls` with the complete set (25 to 27 photos
  in the captured fixtures), and additionally fills `floor_plan_image_url`.

So the harness **does** capture the full additional photo set from the detail
page; it is stored in `ListingDetail.photo_urls`, replacing the summary
thumbnails. If you only ever read a `ListingSummary` (for example from the
shortlist before detail scraping) you see only the thumbnails. The full set is
available after the detail stage of the funnel.

## ListingDetail

`ListingSummary` plus the full text and media fields parsed from the detail
page. Produced by [`parse_detail_page`](parsers.md). Inherits every summary
field; the fields below are the additions and overrides.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `photo_urls` | `list[str]` | (override) | Full detail-gallery photo set, replacing the summary thumbnails. |
| `listing_detail` | `bool` | `False` | True when usable detail data was hydrated successfully. |
| `detail_failure_reason` | `str \| None` | `None` | Operator-safe detail hydration failure reason. |
| `building_name` | `str \| None` | `None` | Canonical building name from structured detail state. |
| `building_structure` | `str \| None` | `None` | Building structure or construction method. |
| `total_units` | `str \| None` | `None` | Displayed total unit count. |
| `contract_period` | `str \| None` | `None` | Displayed contract period. |
| `pickup_features` | `list[str]` | `[]` | Confirmed structured PICK UP features. |
| `remarks` | `str \| None` | `None` | Raw detail remarks. |
| `description` | `str` | `""` | Free-text description (`備考`). |
| `floor_plan_image_url` | `str \| None` | `None` | URL of the floor-plan image (`間取図`), also present in `photo_urls`. |
| `facility_features` | `list[str]` | `[]` | Enabled facility features grouped by category. |
| `structured_detail` | `StructuredDetail \| None` | `None` | Rich server-state detail retained for persistence. |

## LLM projection boundary

The canonical `ListingSummary` and `ListingDetail` models above remain rich and are persisted unchanged, including URLs, internal IDs, age metadata, detail status, and full detail text/media fields. The LLM layer derives a separate compact prompt payload through [`project_property_for_llm`](llm.md#compact-property-projection); that payload is not a replacement model and must never be stored as canonical listing data.

## Recommendation
One ranked recommendation produced by the [`Recommender`](llm.md).

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `listing_id` | `str` | required | Internal ID of the recommended listing. |
| `rank` | `int` (> 0) | required | 1-based rank. |
| `reasons` | `list[str]` | `[]` | Why the listing was recommended. |
| `satisfied_constraints` | `list[str]` | `[]` | Soft preferences the listing satisfies. |
| `violated_constraints` | `list[str]` | `[]` | Soft preferences the listing violates. |
| `probable_negatives` | `list[str]` | `[]` | Disabled features carried through as caveats. |
| `listing` | `ListingSummary \| None` | `None` | The hydrated listing, when available. |

## SearchPlan

The structured plan produced by the [`QueryParser`](llm.md) from a natural
language query, and consumed by the [filter encoder](filters.md).

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `flow` | `Literal["rent", "buy"]` | required | Rental or purchase search flow. |
| `prefecture` | `str` | required | Target prefecture, for example `osaka`. |
| `cities` | `list[str]` | `[]` | Target cities within the prefecture. |
| `hard_filters` | `dict[str, list[str]]` | `{}` | Typed hard filters keyed by canonical field name. |
| `soft_prefs` | `list[str]` | `[]` | Free-text soft preferences used for ranking. |
| `budgets` | `Budgets \| None` | `None` | Optional per-search budget override. |

## FilterOption and FilterMap

The versioned filter map contract (see [filters.md](filters.md) for the schema
and validation rules).

`FilterOption`:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `code` | `str` | required | AtHome filter code, for example `kc123`. |
| `label` | `str` | required | Human-readable option label. |

`FilterMap`:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `version` | `int` | required | Schema version the harness understands (currently `1`). |
| `content_hash` | `str` | required | Content hash used to detect drift. |
| `mappings` | `dict[str, dict[str, list[FilterOption]]]` | required | `(flow, field) -> options`. |

## RunReport

The final report produced by a [`SearchSession`](architecture.md) run.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `query` | `str` | required | Original natural-language query. |
| `plan` | `SearchPlan` | required | The search plan actually executed. |
| `results_seen` | `int` (>= 0) | required | Total listings harvested. |
| `pages_scraped` | `int` (>= 0) | `0` | Number of result pages fetched. |
| `shortlist` | `list[ListingSummary]` | `[]` | The shortlist sent to detail scraping. |
| `recommendations` | `list[Recommendation]` | `[]` | The ranked recommendations. |
| `budgets_consumed` | `Budgets \| None` | `None` | Budgets actually consumed. |
| `partial` | `bool` | `False` | True when the run was cut short by a budget or a block. |
