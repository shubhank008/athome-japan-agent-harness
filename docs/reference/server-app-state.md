# Server app state detail payload

AtHome rental-detail pages embed SSR state in:

```html
<script id="serverApp-state" type="application/json">
```

This is an internal, unversioned view model, not a public API. Parse the script once,
then use only `first-view-ITEMS.propertyData`: the raw request-keyed `G.text.*` value
and `PropertyDetailRentLivingState-STORE-ITEM.view` are byte-for-byte duplicate views
of the same detail response.

## Curated reference captures

Both files were derived from `debug/detail_18_1106831830.html`, after removing session,
transport, duplicated SSR, SEO/navigation, analytics, and explicitly rejected branches.
They contain public listing data only.

| File | Purpose |
|---|---|
| [`data/server-app-state-1106831830.full.json`](../../data/server-app-state-1106831830.full.json) | Retained source-shaped payload. Use to discover fields available for future storage. |
| [`data/server-app-state-1106831830.active.json`](../../data/server-app-state-1106831830.active.json) | Normalized storage/discovery selection. It is deliberately richer than an LLM prompt projection. |

The full reference preserves the retained source topology, while removing rejected
keys and normalizing main-listing images. The active reference additionally renames
selected fields for the internal record and turns nearby-facility image context into a
useful image shape.

## Extraction and source policy

1. Parse `script#serverApp-state` as JSON.
2. Read `first-view-ITEMS.propertyData.rentInfo`.
3. Require `rentInfo.otherPropertyInfo.bukkenNo` to equal the requested AtHome listing
   identifier, with a non-empty property name and price.
4. Persist the active projection. Preserve the full retained raw projection separately
   when source provenance and later reprocessing matter.
5. Use the DOM only as a fallback when this state is absent or invalid.

A challenge page must be detected before either source is trusted or persisted.

## Retained source-shaped schema

The following is the complete retained topology to three or more levels. A leaf is
shown as `string`, `boolean`, `null`, or `array<object>`; all values remain raw AtHome
text unless noted otherwise.

```text
first-view-ITEMS
└── propertyData
    ├── rentInfo
    │   ├── classification: syumokuNm (property type), syumokuNmSouba (market type),
    │   │   syumokuCd (property-type code), syumokuRoman (Romanized type slug)
    │   ├── identity and location: buildingNm, prefectureNm, prefectureRoman,
    │   │   prefectureCd, cityNm, cityRoman, townNm, townCd, address
    │   ├── transit: lineNm, lineRoman, stationNm, stationRoman,
    │   │   access[] { name, url, accessEkiToho (walking time) }
    │   ├── imageList[] { imageUrl, title, subCategory, nearbyFacility? }
    │   ├── pricing: price, deposit, keyMoney, hoshokin (guarantee deposit),
    │   │   managementFee
    │   ├── unit: madori (layout), area, chikunengetsu (construction date),
    │   │   kaidateKai (building and unit floor), lightSurface (orientation),
    │   │   genkyo (occupancy/status), hikiwatashi (move-in timing)
    │   ├── pickup { isSeparateBath, hasBathDryer, hasAutoLock,
    │   │   hasMonitorIntercom, hasDeliveryBox, isFreeInternet, isAbove2nd,
    │   │   isNewBuild, hasPetSodan, hasParking }
    │   ├── panoramaInfo { panoramaUrl, panoramaThumb, panoramaType }
    │   ├── flags: isShinchiku (new build), isMinyukyo (occupied), isShowMap,
    │   │   isNewMark
    │   ├── appealPoint (editorial selling-point text)
    │   ├── buildingInfo { buildingNm, chikunengetsu, tatemonoKozo (structure),
    │   │   sokosu (total unit count), madori, reformLog, renovationLog, biko (remarks) }
    │   └── otherPropertyInfo { jokento (conditions), contract (term), koshinRyo
    │       (renewal fee), chukaiTesuryo (brokerage fee), torihikiTaiyo
    │       (transaction type), energyShohi (energy-consumption performance), dannetsu
    │       (insulation performance), meyasuKonetsuhi (estimated annual energy cost),
    │       kokaiDate (publication date), jikaiKousinData (next update date), bukkenNo
    │       (AtHome listing ID), kanriNo (agency-side management/reference number),
    │       twoPairOtherProperty[] }
    ├── facilityFeatureList[] { title (feature group), text (Japanese comma-separated features) }
    ├── costInfo
    │   ├── initialCostSimulation { preliminary, uchiwake[] (breakdown), chintaiHosho,
    │   │   isFreeRent }
    │   └── costList[] [ { title, text }, ... ]
    ├── surroundingInfo
    │   ├── mapData { ido (latitude), keido (longitude), nearestTrain { ensenInfo[],
    │   │   stationInfo[] }, accessInfo[] { lineName, stationName, tohoJikan,
    │   │   valkenCd, kenEnsenekiCd } }
    │   └── facilityList[] { imageURL, title, kyori (distance), shubetsuNm (category),
    │       serialNo }
    ├── kaiinInfo
    │   ├── identity: syogo (agency name), domain, kaiinNo (AtHome member ID),
    │   │   kaiinLinkNo, syosaiUrl
    │   ├── contact: address, access, telFax, menkyoNo (real-estate licence number)
    │   └── operations: eigyoTime { main, callCrayons }, teikyubi (regular closing day),
    │       teikyubiAndEigyoTime, tokutyou (characteristics), syozokuKyokai
    │       (professional-association memberships)
    └── otherPropertyData[]
        ├── identity: id (AtHome listing ID), seoRoma (flow URL segment), title,
        │   tatemonoNm, type, location, prefCd
        ├── traffic[] { lineName, stationName, tohoJikan (walking minutes), tohoKyori
        │   (walking distance), busKyori, busJikan, busteiNm, busteiKyori, busteiJikan,
        │   lineCd, stationCd, kenEnsenekiCd, valkenCd }
        ├── accessInfo[] { access (optional rendered text), walkingTime }
        ├── contract { price, managementFee, deposit, keyMoney, guaranteeDeposit,
        │   parkingPrice }
        ├── bukkenInfo { chikunengetsu, construction, kaidateKai, madoriTypeNm }
        ├── areaInfo { area, landArea, tsubo, unitPrice, buildingToLandRatio,
        │   floorAreaRatio, saitekiYoto, yotoChiikiNm, shidoMenseki, tochiKenriNm }
        ├── mainImage { url, subCategory, caption, serialNo, status, encrypted }
        ├── images[] { url, subCategory, caption, serialNo, status, encrypted }
        ├── kaiin { syogo, access, urlLong }
        ├── bukkenAccess[] { name, url }
        ├── recommendComment { comment, staffNm, staffRecordNo, memberNm, memberImg,
        │   isOnlineConsultation }
        └── display flags: appeal, isOption, mailInquiryFlag, lineInquiryFlag,
            leasingFlag, ownerChangeDisplayFlag, leasingDisplayFlag, isImg10Over,
            isDetailKaiin, kokaiKbn, kaiinUrl, isShokenKeiyaku,
            isShokenInfoAvailable, tenpoPlusFlg, tenant
```

### Image normalization

The retained references remove `caption`, `serialNo`, `status`, and `encrypted` from
main-listing images. They retain `imageUrl`, `title`, and `subCategory`.

For an image whose source `shuhenKankyo` exists, the active shape is:

```json
{
  "imageUrl": "/image_files/path/...",
  "title": "Facility name 距離：270m",
  "subCategory": "ショッピング施設",
  "nearbyFacility": {
    "name": "Facility name",
    "distance": "距離：270m",
    "category": "ショッピング施設"
  }
}
```

This preserves nearby-facility images while replacing generic source labels such as
`未設定` and `not_set`. `subCategory="layout"` remains the floor-plan signal for future
vision processing.

### Identifier semantics

- `bukkenNo` is the stable AtHome property/listing identifier. It is the primary
  external key and must match the requested detail URL.
- `kanriNo` literally means management number. The payload alone does not document its
  ownership, but its placement and observed value indicate an agency-side property
  reference number. Store it as `agencyPropertyId`, not as a globally unique key.

## Active storage and discovery contract

Use the active capture as a target contract for rich persistence. It supports user
presentation, deterministic filters, vector-enrichment documents, and candidate
prefetch. It is explicitly **not** the compact, consolidated data that is sent to an
LLM.

| Active path | Source path | Intended use |
|---|---|---|
| `listing.athomeListingId` | `rentInfo.otherPropertyInfo.bukkenNo` | Stable external identity and hydration key. |
| `listing.agencyPropertyId` | `...kanriNo` | Agency reference, display/debug only. |
| `listing.classification` | `rentInfo.syumoku*` | Property-type filters and Romanized URL values. |
| `listing.identityAndLocation` | `rentInfo` location fields | Address display, geographic filtering, and Romanized slugs. |
| `listing.transit` | `rentInfo.line*`, `station*`, `access` | Station search and walking-time constraints. |
| `listing.pricing`, `unit`, `flags` | `rentInfo` | Core record, sorting, and deterministic filters. |
| `listing.appealPoint` | `rentInfo.appealPoint` | Search/vector text and user-facing context. |
| `listing.images` | normalized `rentInfo.imageList` | Gallery, layout detection, nearby-facility images. |
| `listing.buildingInfo`, `contractAndPublication` | `rentInfo` nested objects | Building/contract detail and freshness policy. |
| `listing.facilityFeatureList` | `propertyData.facilityFeatureList` | Amenity text, search, and vector enrichment. |
| `listing.costInfo` | `propertyData.costInfo` | Upfront-cost display/estimation. |
| `listing.surroundingInfo.mapData`, `.facilityList` | `propertyData.surroundingInfo` | Coordinates, nearby-facility and station/convenience-store queries. |
| `agency` | selected `kaiinInfo`, keyed by `kaiinNo` | Separate deduplicated agency entity with name, address, contact, licence, and hours. |
| `listing.agencyKaiinNo` | `kaiinInfo.kaiinNo` | Listing-to-agency relationship; do not duplicate the agency profile per listing. |
| `recommendedPropertyCards[]` | normalized `otherPropertyData[]` | Candidate discovery records persisted as `summary_complete`. |

## Entity and lifecycle contract

The database is deliberately richer than an LLM input. Keep listings, their transit,
facilities, images, costs, structured details, and source snapshot data available for
future filters and product features. Build a compact LLM projection only at inference
time.

`kaiinInfo` becomes a separate `Agency` entity keyed by `kaiinNo`. A full-detail
capture may upsert its complete contact profile and link the listing through
`agencyKaiinNo`. A recommendation card has only a partial agency reference, so it may
establish the relationship but must not overwrite a complete agency profile.

Listing completeness is explicit:

| State | Source | Meaning |
|---|---|---|
| `summary_partial` | Search-results page | Dated broad-search observation with limited listing fields. |
| `summary_complete` | `otherPropertyData` | Rich normalized card usable for recommendation, similarity, and preliminary filtering. |
| `detail_complete` | Validated canonical detail state | Full structured property record, linked agency profile, and detailed source snapshot. |

A confirmed unavailable/deleted detail page is removed, including its related listing
records and pending hydration job. Challenge pages, blocks, timeouts, and transient
HTTP failures are never deletion evidence: they only record a retryable job failure.

## Recommendation-card strategy

`otherPropertyData` is valuable, but it is a **20-card recommendation/listing-summary
shape**, not a detailed property payload. In this capture all 20 cards have unique
AtHome IDs and none is the target listing `1106831830`.

Normalize every card to the existing listing summary contract and persist it immediately
as `summary_complete`. This is richer than a search-result observation
(`summary_partial`) and is sufficient for recommendation and “more like this” cards.
It must never overwrite a richer `detail_complete` record.

Enqueue an idempotent background hydration job keyed by `id` when no detail record is
fresh. At job-claim time, the worker re-reads the record: it skips the job if a live
request has already refreshed the detail, otherwise it fetches the canonical URL formed
from `seoRoma` and `id`, validates the returned ID, and upgrades the record to
`detail_complete`.

The live LLM pipeline is deliberately separate from this queue. It checks whether a
fresh detail record is available; if not, it performs its own direct detail fetch,
upserts the result, and uses that fresh result immediately. A background worker never
blocks or supplies a stale substitute for that live path.

Do not fetch a detail page merely to discard a rich card. The card already supplies
identity, URL flow, title/location, up to three routes, rent/deposit/key-money/fee,
layout, floor, construction date/type, area, images, agency name/access, and flags.

It still requires a detail fetch for these main-record fields:

- Full formatted address and address component names/romanizations.
- Coordinates, map access, nearby-facility list, and nearby-facility image context.
- `facilityFeatureList`, PICK UP amenity booleans, and the free-text `appealPoint`.
- Occupancy/status and move-in timing.
- Building structure, total unit count, renovation/reform history, and remarks.
- Contract term, brokerage/renewal fee, transaction type, energy/insulation values,
  publication/update dates, and the agency management number.
- Full agency address, telephone/fax, licence, hours, and closing day.
- Main-detail image semantics such as `shuhenKankyo`; card images have only generic
  media metadata.

Recommendation cards therefore reduce future live scraping substantially, but cannot
be treated as full detail records or as authoritative current availability.

## Freshness and background hydration

The initial cache policy is a single, conservative rule:

```text
detail_fresh_until = successful canonical-detail fetch time + 14 days
```

A live LLM request may use a `detail_complete` record only while it is fresh. Otherwise
it fetches the canonical detail page directly, upserts the result, and continues with
that result. It does not wait for, lease, or otherwise depend on the background queue.

Background workers operate FIFO for now. Each worker atomically claims one deduplicated
job, rechecks freshness and deletion before requesting the page, fetches/parses/updates
one listing, then sleeps according to the shared scraper limiter. A worker must:

- Mark a job successful when another live request already produced fresh detail.
- Delete the listing and all related listing records only after a positively identified
  unavailable/deleted page, using documented page selectors/markers.
- Record a retryable failure and stop or cool down on challenge/block markers, rather
  than retrying aggressively.
- Record transient transport/parser failures with bounded backoff; they are not
  deletion evidence.

Workers are intentionally lean, standalone consumers of the durable queue: claim, fetch,
validate, parse, upsert, and acknowledge. They must use the existing `BaseScraper`,
challenge detection, proxy policy, and shared rate-limit/budget controls.

Horizontal deployment is an operations concern, not a way to evade a target's controls.
A deployment may add workers only under a globally coordinated request budget, explicit
provider/site authorization where required, and block-aware circuit breaking. A block or
challenge pauses the affected worker/pool and is reported to operations; increasing
containers, hosts, or IPs to continue around that signal is prohibited.

Search-result pages are not long-lived query-result cache entries because arbitrary
filter combinations produce different, fast-changing result sets. Their individual
listing observations are still stored as `summary_partial`. A future short-lived cache
may key rendered search results by the complete normalized parameter query, but it is
not part of this initial worker design.

## Explicitly disregarded paths

```text
root.G.text.*
root.storeApp-STORE-ITEM
root.storeSearch-STORE-ITEM
root.storePropertyDetail-STORE-ITEM
root.storeCitySearch-STORE-ITEM
root.SSR-ITEMS
root.delayScript-ITEMS
root.PropertyDetailRentLivingState-STORE-ITEM
first-view-ITEMS.breadcrumbData
first-view-ITEMS.bkeContentsData
propertyData.heyaNayoseKaiinList
propertyData.dimension
propertyData.surroundingInfo.townLibraryInfo
propertyData.surroundingInfo.madoriSouba
propertyData.kaiinInfo.rentList
propertyData.kaiinInfo.buyList
propertyData.kaiinInfo.qaList
propertyData.kaiinInfo.osusumeComment
propertyData.kaiinInfo.staffList
propertyData.kaiinInfo.campaignText
propertyData.kaiinInfo.* inquiry, booking, and UI-capability flags
first-view-ITEMS.schemaData
```

The browser/session identifiers, cookies, headers, and transport state are not copied
into either reference artifact.

## Validation and fallback

Accept structured data only when the script parses, `rentInfo` is an object, its
`bukkenNo` matches the requested listing ID, and it has a name, a non-empty price, and
address or station data. Otherwise emit a debug-safe source marker and use the DOM
parser fallback.
