# Parsers

The HTML-to-model building blocks under `src/athome_harness/scraping/`:
`list_parser.py` and `detail_parser.py`. Both are pure functions over HTML text
(injected into the harvester and the detail stage of the funnel), so they run
offline over captured fixtures and never touch the network.

* **Depends on:** [data models](data-models.md) (`ListingSummary`,
  `ListingDetail`, `PriceBreakdown`).
* **Depended on by:** the [architecture funnel](architecture.md)
  (`SearchSession` injects `parse_list_page` into the `Harvester` and calls
  `parse_detail_page` per shortlisted listing) and the operator probes.

## parse_list_page

**Signature:** `parse_list_page(html: str, ref_date: date | None = None) ->
list[ListingSummary]` (`scraping/list_parser.py`).

Parses one AtHome list page. The page is organized as building blocks, each
holding one or more unit detail boxes; a multi-unit building yields several
`ListingSummary` values that share the building identity. `ref_date` anchors
building-age computation (defaults to today, injectable for deterministic
tests).

Internal structure:

* `BuildingBlock` (dataclass): the per-building shared context extracted from
  the building heading (title, address, station, walk minutes, building type,
  age, building URL).
* `_parse_unit(unit, block) -> ListingSummary | None`: parses one unit box.
  Returns `None` (with a warning) when the unit has no `data-bukken-no`
  identity; only that unit is skipped.

Behavior notes:

* Prices are stated in 万円 (ten-thousand yen) on the page and converted to yen.
* Month-based deposit/key-money terms (`1ヶ月`) are preserved in
  `deposit_raw` / `key_money_raw`; the yen value is recorded as `0` with a
  warning (never silently conflated with "no deposit").
* Enabled facility items become `usp_tags`; items carrying the disabled class
  become `probable_negatives`.
* Photos are the inline thumbnails of the unit block (about 6 per unit); the
  full gallery set is captured later by the detail parser (see
  [data models](data-models.md#photo-coverage-summary-vs-detail)).

## parse_detail_page

**Signature:** `parse_detail_page(html: str, ref_date: date | None = None) ->
ListingDetail` (`scraping/detail_parser.py`).

Parses one AtHome property detail page. Identity comes from the `<title>`
numeric suffix (`_extract_key`), falling back to the canonical URL; a page with
no stable key is rejected with a warning. Data fields come from the
`table.dataTbl` rows (label to value), the payment block, the photo gallery, and
the facility tables.

Behavior notes:

* `photo_urls` is the **full gallery set** (25 to 27 photos in the captured
  fixtures), overriding the summary thumbnails, and `floor_plan_image_url` is
  the `間取図` item (also present in `photo_urls`).
* Facility rows are grouped by category (`バス・トイレ`, `キッチン`, `収納`,
  `設備・サービス`, `TV・通信`, `その他`); enabled items become
  `facility_features`, disabled items become `probable_negatives`.
* Month-based deposit/key-money terms are preserved as raw text exactly like
  the list parser.

## Challenge detection

`scraping/challenge.py` exposes
`detect_athome_challenge(body: str) -> str | None`. It recognizes the AtHome
puzzle markers (`Click to verify`, the cookie/JavaScript requirement) and the
Japanese authentication heading. Both parsers' callers (the harvester, the
probes, the refarmer boundary) use it to fail closed: a challenge page is never
parsed as data and never saved as a fixture. Reuse this detector instead of
re-declaring markers so the safety boundary cannot drift.

## DOM access map

The authoritative selector-by-selector contract lives in
`docs/specs/001-athome-home-finder/spec.md` under "DOM Access Map". It is the
living parser contract: any selector, label, ancestor-scope, or field-shape
change must update the map, the relevant fixture, and a regression test
together. The map covers:

* List page: building heading and hint list, unit detail box
  (`div.p-property__room--detailbox[data-bukken-no]`), rent row, deposit and
  key money, floor plan and area, facility lists, photos, and detail URL
  construction.
* Detail page: `table.dataTbl` label rows, the payment block
  (`div.paymentInfo.typeChintai dl.data`), the gallery
  (`#detail-image_view ul.zoomList`), USP points, and facility categories.

## Fixtures

`tests/fixtures/` holds the live-captured regression fixtures:
`osaka_rental_list.html` (460 units) and three detail pages
(`detail_1101570928.html`, `detail_1122949022.html`, `detail_1131157822.html`),
plus the filter-map extraction sources and the golden report files. A live
capture must be validated as real page content before becoming a fixture; a
challenge page must never be saved as one.
