# Building and unit aggregation

The current pipeline treats each AtHome room/listing as a `ListingSummary` or
`ListingDetail`. That is intentionally preserved because rent, floor, area,
contract terms, and availability can differ between units in one building.
Simple building-level deduplication would lose those distinctions.

## Proposed domain shape

The long-term aggregate is:

```text
Building
  identity and shared metadata
  units: list[Unit]
```

A `Unit` retains the current listing identity and all unit-specific fields. A
`Building` owns only values observed to be shared by its units.

### Building identity

Use a normalized identity built after detail hydration, in this order:

1. A stable AtHome building identifier when the structured payload exposes one.
2. A normalized tuple of canonical building name, address, and prefecture.
3. A conservative fallback that includes the building type and structure.

Never group by title alone. Similar names can represent separate buildings, and
missing identity components must not create an aggressive false merge.

### Shared building metadata

The building aggregate may contain:

- canonical building name;
- address and prefecture/city;
- building type and structure;
- construction date, raw age, rounded age, and display age;
- total floors;
- displayed total unit count;
- shared transport options;
- shared remarks and facility features;
- source IDs and hydration timestamps.

### Unit metadata

Each unit remains separate and contains:

- AtHome listing ID and canonical detail URL;
- room/floor and orientation;
- rent and management fee;
- raw and numeric deposit/key-money terms;
- layout and area;
- contract period;
- unit-specific pickup features and probable negatives;
- photos and floor-plan image;
- detail hydration status and failure reason.

## Pipeline placement

Aggregation belongs after detail hydration and before any building-aware ranking:

```text
list-page summaries
  -> shortlist summaries
  -> detail fetch and hydration
  -> conservative Building -> Unit grouping
  -> building-aware ranking/reporting
```

Do not aggregate before detail hydration. The detail payload supplies the best
building identity and shared metadata, while pre-detail grouping risks merging
unrelated units or losing unit-specific fields.

The existing summary/listing APIs remain valid during migration. A future
adapter can expose aggregates to the recommender while retaining the current
unit-level store rows and URLs for feedback commands.

## Ranking policy

A building-aware recommender should rank buildings and select representative
units without hiding alternatives. The report should show:

- the selected building rank;
- the representative unit and why it was selected;
- other available units in the same building, ordered by rent/floor or the
  user's explicit preference;
- unit-level differences that affect the decision.

If the user explicitly requests a floor, price, or unit condition, those remain
hard unit-level constraints. Otherwise, the default representative unit policy
must be explicit and deterministic rather than silently discarding units.

## Migration boundary

The first implementation should be a pure grouping/value-object layer with
fixture tests. It should not change list harvesting, detail hydration, store
schemas, or recommendation output until the identity collision cases are
covered. A later schema migration can persist buildings separately while
continuing to persist every unit for precise saves, rejects, and URLs.
