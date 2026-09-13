# Optimized recommender input schema analysis

Source: debug/llm_recommender_input.json from the optimized 2026-09-13 run.

Property objects: 20
User prompt characters: 116079

| Key | Present | Serialized characters |
|---|---:|---:|
| address | 20/20 | 304 |
| age | 20/20 | 67 |
| age_display | 20/20 | 291 |
| age_raw | 20/20 | 180 |
| agency | 20/20 | 61970 |
| agency_reference | 4/20 | 140 |
| area_m2 | 20/20 | 97 |
| athome_key | 20/20 | 240 |
| building_name | 17/20 | 321 |
| building_structure | 20/20 | 93 |
| building_type | 20/20 | 175 |
| completeness | 20/20 | 340 |
| construction_date | 20/20 | 180 |
| contract_period | 20/20 | 80 |
| description | 20/20 | 6096 |
| detail_failure_reason | 0/20 | 80 |
| detail_fetched_at | 20/20 | 680 |
| detail_fresh_until | 20/20 | 680 |
| facility_features | 20/20 | 3894 |
| floor_plan | 20/20 | 188 |
| floor_plan_image_url | 0/20 | 80 |
| floors | 20/20 | 214 |
| internal_id | 20/20 | 240 |
| listing_detail | 20/20 | 80 |
| pickup_features | 20/20 | 1224 |
| price | 20/20 | 2271 |
| probable_negatives | 13/20 | 750 |
| remarks | 20/20 | 6096 |
| source_data | 4/20 | 22059 |
| station | 20/20 | 82 |
| title | 20/20 | 383 |
| total_units | 20/20 | 66 |
| url | 20/20 | 920 |
| usp_tags | 4/20 | 128 |
| walk_minutes | 20/20 | 73 |

## Observations

The shared compact projection removes agency, URLs, cache/lifecycle metadata, internal IDs, duplicate age fields, and detail status before both ranking stages.

Review bounded description, facility_features, pickup_features, probable_negatives, and nested price values next. This artifact is operator evidence, not an LLM input.
