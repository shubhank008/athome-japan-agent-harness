# Recommender input schema analysis

Source artifact: `debug/llm_recommender_input.json` from the 2026-09-13 low-effort run.

The prompt contained 20 listing objects and approximately 86,285 user-prompt characters.
The serialized listing fields were:

| Key | Present | Approximate total characters | Initial assessment |
|---|---:|---:|---|
| `agency` | 19/20 | 56,270 | Very large repeated agency object. Strongest trimming candidate; pass agency identity/contact summary only or remove from ranking prompt. |
| `description` | 19/20 | 4,620 | Potentially useful narrative, but should be bounded/trimmed. |
| `remarks` | 19/20 | 4,622 | Potentially useful, but overlaps detail text and should be bounded. |
| `facility_features` | 19/20 | 2,858 | Useful for amenity ranking; keep in compact form. |
| `price` | 20/20 | 2,278 | Core ranking data; retain. |
| `probable_negatives` | 18/20 | 1,037 | Useful caveats; retain compactly. |
| `url` | 20/20 | 920 | Not needed for reasoning; keep in final report, omit from ranking prompt. |
| `detail_fetched_at` | 19/20 | 650 | Provenance only; omit from reasoning prompt. |
| `detail_fresh_until` | 19/20 | 650 | Cache metadata only; omit from reasoning prompt. |
| `building_name` | 14/20 | 244 | Useful identity/context; retain if needed. |
| `address` | 20/20 | 294 | Location ranking data; retain. |
| `title` | 20/20 | 359 | Core identity; retain. |
| `floor_plan` | 20/20 | 224 | Core hard-filter/ranking data; retain. |
| `floors` | 20/20 | 202 | Useful ranking data; retain. |
| `age_display` | 20/20 | 305 | Useful building-age context; retain compactly. |
| `age_raw` | 20/20 | 180 | Duplicate/raw representation; likely omit when `age_display` exists. |
| `construction_date` | 20/20 | 180 | Useful if age is not normalized; otherwise potentially redundant. |
| `building_structure` | 19/20 | 91 | Compact useful building fact; retain. |
| `building_type` | 20/20 | 171 | Compact useful fact; retain. |
| `total_units` | 19/20 | 71 | Compact useful fact; retain. |
| `contract_period` | 19/20 | 80 | Useful contract fact; retain if relevant. |
| `pickup_features` | 19/20 | 831 | Useful normalized amenities; retain. |
| `station` | 20/20 | 88 | Core location/ranking data; retain. |
| `walk_minutes` | 20/20 | 77 | Core ranking/filter data; retain. |
| `area_m2` | 20/20 | 98 | Core ranking/filter data; retain. |
| `internal_id` | 20/20 | 240 | Needed to map model output; retain. |
| `athome_key` | 20/20 | 240 | Needed for identity; possibly combine with `internal_id` when equal. |
| `completeness` | 20/20 | 340 | Routing/provenance metadata; omit from reasoning unless ranking detail quality. |
| `listing_detail` | 20/20 | 81 | Routing/provenance metadata; omit from reasoning. |
| `detail_failure_reason` | 1/20 | 153 | Only useful as a confidence caveat; retain only when non-null. |
| `floor_plan_image_url` | 0/20 | 80 | Empty in this capture; omit. |
| `source_data` | 0/20 | 40 | Empty in this capture; omit. |
| `agency_reference` | 0/20 | 80 | Empty in this capture; omit. |
| `usp_tags` | 0/20 | 40 | Empty in this capture; omit. |

## Main finding

`agency` alone contributes approximately 56,270 serialized characters, around 65% of the
user prompt in this capture. Agency data should not be sent as a full nested entity to
the ranking model. A compact projection could retain only:

```text
agency_name
agency_access
```

or omit agency entirely from shortlisting and reserve it for the final report.

The next prompt-reduction experiment should compare:

1. Current full `ListingDetail` serialization.
2. Compact ranking projection retaining identity, location, transit, price, layout, area,
   age, building facts, amenities, caveats, and bounded description/remarks.
3. A final-report projection that adds agency and URLs after ranking.

This is an analysis artifact, not yet a production prompt change.
