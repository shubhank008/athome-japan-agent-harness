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

Monetary breakdown for one unit. All yen amounts are integers; month-based
upfront terms are preserved as raw text so they are never indistinguishable
from zero.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `rent` | `int` (>= 0) | required | Monthly rent in yen. |
| `management_fee` | `int` (>= 0) | `0` | Monthly management fee in yen. |
| `deposit` | `int` (>= 0) | `0` | Upfront deposit in yen. |
| `key_money` | `int` (>= 0) | `0` | Upfront key money in yen. |
| `deposit_raw` | `str \| None` | `None` | Raw deposit term (for example `1ヶ月`) when it is not a plain yen value. |
| `key_money_raw` | `str \| None` | `None` | Raw key-money term when it is not a plain yen value. |

The `*_raw` fields exist because AtHome expresses some deposits and key money as
a count of months (`1ヶ月`) rather than a yen figure. The parser records the yen
value as `0` and keeps the raw term so a month-based term is never mistaken for
"no deposit". Converting a month term to yen requires the unit's rent, which the
parser does not assume.

## ListingSummary

One unit of a building. A multi-unit building yields several summaries that
share a building identity but differ per unit. Produced by
[`parse_list_page`](parsers.md).

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `internal_id` | `str` | required | Stable internal property ID used for dedupe. |
| `athome_key` | `str` | required | AtHome `BKLISTID` listing key. |
| `url` | `str` | required | Canonical AtHome listing URL. |
| `title` | `str` | required | Human-readable listing title. |
| `address` | `str` | required | Street/presented address of the unit. |
| `station` | `str \| None` | `None` | Nearest station name, when known. |
| `walk_minutes` | `float \| None` | `None` | Walking minutes to the station. |
| `building_type` | `str \| None` | `None` | Building category label. |
| `floors` | `str \| None` | `None` | Floor/build-height descriptor, raw text. |
| `age` | `float \| None` | `None` | Building age in years, when exposed. |
| `price` | `PriceBreakdown` | required | Monetary breakdown for the unit. |
| `floor_plan` | `str \| None` | `None` | Layout descriptor (for example `1LDK`). |
| `area_m2` | `float` | required | Floor area in square metres. |
| `usp_tags` | `list[str]` | `[]` | Confirmed feature highlights (enabled facilities). |
| `probable_negatives` | `list[str]` | `[]` | Disabled features surfaced as caveats. |
| `photo_urls` | `list[str]` | `[]` | Photo URLs known at this stage (see note below). |

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
| `description` | `str` | `""` | Free-text description (`備考`). |
| `floor_plan_image_url` | `str \| None` | `None` | URL of the floor-plan image (`間取図`), also present in `photo_urls`. |
| `facility_features` | `list[str]` | `[]` | Enabled facility features grouped by category. |

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
