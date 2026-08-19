# Marker Contract 001: AtHome Japan Home Finder

The exact `[MARKER]` strings the harness prints to prove a search session worked, and
the failure patterns that must NOT appear. The e2e rig (tests/e2e/test_search_session.py)
greps for these verbatim. One marker per line, key=value pairs after the marker.
All values are examples; tests match on marker name and required keys, not exact values.

## Happy-path markers (must appear, in this relative order)

```
[SESSION_START] session_id=<uuid> flow=rent prefecture=osaka
[SEARCH_PLAN] hard_filters=<n> soft_prefs=<n> ambiguous=<true|false>
[CLARIFY] question=<text>                          # only when ambiguous=true
[FILTER_ENCODE] params=<n> unmapped=<n>            # unmapped must be 0 on happy path
[HARVEST_START] expected_pages=<n> max_pages=<n>
[HARVEST_PAGE] page=<n> listings=<n> elapsed_s=<f> # repeats per page
[HARVEST_DONE] pages=<n> listings=<n> partial=<true|false>
[SHORTLIST_START] candidates=<n> batch_size=<n>
[SHORTLIST_DONE] shortlisted=<n> tokens=<n>
[DETAIL_START] targets=<n>
[DETAIL_DONE] scraped=<n> failed=<n>               # failed must be 0 on happy path
[REPORT] top_y=<n> md=<path> json=<path>
[STORE] seen=<n> new=<n> rejected_excluded=<n>
[SESSION_END] status=ok elapsed_s=<f> total_tokens=<n>
```

## Proxy and degradation markers (appear only when triggered)

```
[ATHOME_CHALLENGE] url=<redacted> kind=<puzzle|javascript> htmlLength=<n>
[BLOCK_DETECTED] url=<redacted> signature=<403|429|captcha>
[PROXY_ROTATE] attempt=<n> of=<n>                  # attempt <= 3 (spec budget)
[PROXY_RECOVERED] via=proxy
[BUDGET_HIT] kind=<pages|runtime|tokens> limit=<n>
[PARTIAL_REPORT] reason=<budget|block> listings=<n>
```

## Refarm orchestration markers (SessionRefarmer, M7 / T27)

The bounded browser refarm loop wraps the synchronous HTTP adapter. It runs a
direct attempt first, then farms a fresh browser session on block and retries
once (bounded by `max_refarms`). These markers prove that loop ran in order;
the M7 integration test asserts their relative order and that the forbidden
failure patterns never appear alongside them.

```
[REHANDOFF_TRIGGERED] url=<redacted> signature=<403|429|captcha> refarms=<n>
[REHANDOFF_FARMED] proxy=<identity|direct> cookies=<n>
[REHANDOFF_STILL_BLOCKED] url=<redacted> signature=<403|429|captcha>
[CURL_HANDOFF_BOUND] proxy=<identity|direct> cookies=<n> impersonate=<profile>
[CURL_BLOCK_REHANDOFF] url=<redacted> signature=<403|429|captcha>
```

The adapter-internal `[CURL_REQUEST]` line records each in-flight request and is
expected, but is not a contract marker on its own. In a successful direct-first
recovery the markers must appear in this relative order:

```
[BLOCK_DETECTED] -> [REHANDOFF_TRIGGERED] -> [REHANDOFF_FARMED] -> [CURL_HANDOFF_BOUND]
```

When the rebound attempt is still blocked, `[REHANDOFF_STILL_BLOCKED]` appears
after `[REHANDOFF_FARMED]` and the original block is re-raised without further
farming (bounded by `max_refarms`).

## Maintenance-tool markers (tools/dump_filter_map.py)

```
[FILTERMAP_START] flows=rent,buy
[FILTERMAP_OK] version=1 filters_rent=<n> filters_buy=<n> hash=<sha256[:12]>
[FILTERMAP_BROKEN] flow=<rent|buy> filter=<name> reason=<schema|selector|empty>
[FILTERMAP_ISSUE_FILED] url=<issue_url>            # CI only
```

## Failure patterns (must NEVER appear in any test run)

```
Traceback (most recent call last)
LLM_JSON_INVALID
UNKNOWN_FILTER_ENCODED          # encoder guessed instead of raising
PROXY_CREDENTIALS_IN_URL_LOG    # secret leak guard
FILTER_MAP_SCHEMA_UNSUPPORTED
```

## Rules

- Marker names are stable API: changing one is a breaking change requiring contract edit
  first, implementation second.
- No marker may contain secrets, full URLs with query strings, or listing PII beyond
  AtHome's own public listing ID.
- `[SESSION_END] status=ok` is mandatory for the happy path; any other status
  (`partial`, `aborted`) requires its corresponding degradation marker to have appeared.
