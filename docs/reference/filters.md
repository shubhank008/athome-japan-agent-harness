# Filter map and encoder

The versioned bridge between a structured [`SearchPlan`](data-models.md#searchplan)
and AtHome's server-side POST parameters, under `src/athome_harness/filters/`.

* **Depends on:** [data models](data-models.md) (`SearchPlan`, `FilterMap`,
  `FilterOption`).
* **Depended on by:** the [architecture funnel](architecture.md)
  (`SearchSession` calls `encode_plan` after plan confirmation) and the
  [QueryParser](llm.md#queryparser) (summarizes the map into its prompt).

The canonical field conditions are VERIFIED against live dumps of the rent
(chintai/osaka/list, 2026-07-08) and buy (mansion/tokyo/list) list pages. The
checked-in snapshot lives at `filters/data/filter_map.v1.json`, re-extracted
weekly by `.github/workflows/filter-map.yml`.

## FieldCondition

`filters/map_schema.py` owns the per-field metadata. Each entry is a frozen
dataclass:

| Field | Type | Meaning |
|-------|------|---------|
| `cardinality` | `Literal["single", "multi", "range", "bool"]` | How the encoder builds POST params. |
| `control` | `Literal["select", "checkbox"]` | HTML control family the dump tool extracts from. |
| `code_regex` | `re.Pattern[str] \| None` | Validates option codes; `None` for code-less fields. |
| `html_base` | `str` | DOM base name of the field. |
| `pair` | `tuple[str, str] \| None` | The two real HTML fields a `range` field maps to. |
| `aliases` | `tuple[str, ...]` | Accepted alternate names in a plan. |
| `monotonic` | `bool` | True for magnitude-ordered lineages (price, area, age). |

## Rent flow conditions (canonical)

| Field | Cardinality | Code pattern | Notes |
|-------|-------------|--------------|-------|
| `PRICEFROM` / `PRICETO` | single | `^kc\d+$` | Monotonic; the logical `PRICE` range maps onto this pair. |
| `MENSEKI` | single | `^kt\d+$` | Minimum area thresholds. |
| `EKITOHO` | single | `^ke\d+$` | Walking-minutes thresholds. |
| `CHIKUNENSU` | single | `^kn\d+$` | Building age. |
| `KEIYAKU` | single | `^ki\d+$` | Contract terms. |
| `SORT` / `TATEMONONUM` | single | none | Numeric, code-less. |
| `PRICE` | range | via pair | Aliases: `PRICE_RANGE`, `rent price`. |
| `MADORI` | multi | `^km\d+$` | Floor plan. |
| `PRICEOPT` | multi | `^kc2\d\d$` | Rent-related options. |
| `SHUMOKU` | multi | `^kb\d+$` | Property type. |
| `TATEKOUZOU` | multi | `^kh\d+$` | Building structure. |
| `SYUHENKANKYO` | multi | `^kw\d+$` | Surrounding environment. |
| `GAZO` | multi | `^kg\d+$` | Image options. |
| `KODAWARI` | multi | `^[A-Za-z]{1,3}\d*$` | 103 specific requirements (SPEC 1.2). |
| `APPEAL` / `RENOVATION` | bool | `^ka\d+$` / `^ak\d+$` | Toggles. |

The buy flow mirrors these with its own code prefixes (for example price codes
are `kp` on buy, `kt4xx` for `MENSEKITO`), which is why every lookup is keyed by
`(flow, field)`: the same filter name maps to different code prefixes per flow
and codes never collide across contexts.

## validate(filter_map) -> FilterMap

`map_schema.validate` enforces the contract before the map is trusted:

* Exactly the expected flows (`rent` and `buy`) are present; a missing entire
  flow is rejected, not just malformed fields within a present flow.
* `version` equals `SUPPORTED_SCHEMA_VERSION` (1); anything else raises
  `UnsupportedSchemaVersionError`.
* Per field: no duplicate codes, no empty labels, codes match the field's
  `code_regex`, and monotonic lineages never decrease in magnitude.

## encode_plan(plan, filter_map) -> list[tuple[str, str]]

`filters/encoder.py` converts `plan.hard_filters` into ordered POST pairs.

**Signature:** `encode_plan(plan: SearchPlan, filter_map: FilterMap) ->
list[tuple[str, str]]`.

Behavior:

1. Defensively re-checks the schema version and refuses a version it cannot
   trust.
2. For each hard filter, resolves the canonical name or alias against the
   **plan's flow** conditions.
3. Encodes by cardinality:
   * `single`: exactly one value, `FIELD=<code>`.
   * `multi`: zero or more, repeated `FIELD[]=<code>` entries (order preserved).
   * `range`: a from/to pair, mapped onto `condition.pair` (for example
     `PRICE` becomes `PRICEFROM`/`PRICETO` params).
   * `bool`: a true/false toggle encoded as the field's single code.
4. Every emitted code must exist in the map; anything unmappable raises
   `UnknownFilter` or `UnknownFilterValue` (both `ValueError` subclasses), and
   the `[FILTER_ENCODE] params=<n> unmapped=0` marker is logged only on success.

The returned list preserves repeated keys so callers can build a faithful
`application/x-www-form-urlencoded` body.

## Maintenance

`tools/dump_filter_map.py` re-extracts the map from a live list page. The
weekly GitHub Action re-runs the dump and files an issue on DOM drift by
comparing the `content_hash`. When AtHome changes markup, update the dump, the
map snapshot, and the parser tests in the same change (see the DOM access map
checklist in `docs/specs/001-athome-home-finder/spec.md`).
