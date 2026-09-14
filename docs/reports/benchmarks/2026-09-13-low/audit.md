# Live full-probe audit report

## Executive summary

A bounded live full-probe run was executed on 2026-09-13 using the repository-local `.env`.
The run completed with a partial result and produced a final report.

The run demonstrates that the primary pipeline is operational through:

```text
local environment
-> provider construction
-> flow detection
-> query parsing
-> AtHome harvesting
-> challenge detection and browser handoff
-> shortlist scoring
-> detail fetching and parsing
-> recommendation ranking
-> report generation
-> SQLite persistence
```

The main remaining operational limitation is AtHome challenge/refarm reliability for
some detail requests. One detail request failed at handoff validation; the other detail
requests completed through the rebound HTTP path. The browser binary was available in
this run.

## Run identity and configuration

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Query | `cheap 1K in Osaka` |
| Probe | `scripts/full_run_probe.py --mode live` |
| Provider | OpenCodeGo |
| Model | GLM-5.2 |
| Reasoning effort | Local `.env` value at run time |
| Max token budget | Local `.env` value at run time |
| Store | Local SQLite `athome.db` |
| Work directory | `/tmp/athome-full-probe-live-audit` |
| Proxy | Direct connection; Webshare credentials absent |

The probe command was:

```bash
set -a
. ./.env
set +a
PYTHONPATH=src .venv-verify/bin/python \\
  scripts/full_run_probe.py \\
  --mode live \\
  --query "cheap 1K in Osaka" \\
  --work-dir /tmp/athome-full-probe-live-audit \\
  --keep-outputs
```

No credentials or secret values are included in this report.

## Final outcome

```text
status: partial
results_seen: 389
pages_scraped: 10
shortlist: 20
recommendations: 5
detail scraped: 20
detail failed: 1
```

Reports:

```text
/tmp/athome-full-probe-live-audit/reports/report-37f9c13d-2214-4171-bb9f-97aa2a9a35f8.md
/tmp/athome-full-probe-live-audit/reports/report-37f9c13d-2214-4171-bb9f-97aa2a9a35f8.json
```

## Stage-by-stage flow

### 1. Session startup

The session initialized successfully through the configured OpenCodeGo provider.

### 2. Flow detection

The first structured call classified the query as rental:

```json
{"flow":"rent"}
```

### 3. Query parsing

The parser produced:

```text
flow: rent
hard filters: 2
soft preferences: 1
ambiguous: false
```

The query was interpreted as a broad Osaka rental search with a 1K layout and a cheap
rent preference. The parser did not request clarification.

### 4. Filter encoding

The filter encoder resolved the structured intent successfully:

```text
[FILTER_ENCODE] params=2 unmapped=0
```

### 5. AtHome harvesting

The first five direct requests completed, reaching 324 observed listings. AtHome then
returned a challenge:

```text
[ATHOME_CHALLENGE] kind=<puzzle>
[BLOCK_DETECTED] signature=<captcha>
[REHANDOFF_TRIGGERED]
```

The browser handoff succeeded:

```text
[PLAYWRIGHT_RENDERED] html_chars=<5149135> blocked=<false>
[PLAYWRIGHT_SESSION_STATE_SAVED]
[PLAYWRIGHT_HANDOFF_SAVED]
[REHANDOFF_FARMED]
[CURL_HANDOFF_BOUND]
```

Harvesting continued through the configured ten-page limit:

```text
page 6: 339 listings
page 7: 356 listings
page 8: 364 listings
page 9: 377 listings
page 10: 389 listings
```

The result was marked partial because the configured page budget was reached.

### 6. Shortlisting

The shortlister processed:

```text
candidates: 389
batches: 13
successful batches: 13
failed batches: 0
shortlist size: 20
```

Aggregate shortlist usage was approximately:

```text
prompt tokens: 108,117
completion tokens: 18,502
```

### 7. Detail hydration

The detail stage attempted twenty shortlisted listings:

```text
scraped: 20
failed: 1
```

Most detail pages used the rebound HTTP session successfully. One request encountered
a challenge and browser-handoff validation failure.

### 8. Recommendation ranking

The final recommender completed successfully:

```text
recommendations: 5
```

### 9. Persistence

Cumulative local SQLite counts after the run were:

```text
listings:        1,449
agencies:           24
hydration_jobs:    740
searches:            6
recommendations:     8
```

These counts include prior local probe runs. The database is local evidence and remains
untracked.

## Evidence artifacts

Fresh report artifacts:

```text
/tmp/athome-full-probe-live-audit/reports/*.md
/tmp/athome-full-probe-live-audit/reports/*.json
```

Fresh provider and handoff artifacts were written under `debug/`, including:

```text
debug/llm_provider_response_*.json
debug/llm_recommender_input.json
debug/llm_recommender_output.json
debug/llm_repair_input.json
debug/llm_repair_output.json
debug/session_state.json
debug/live_last_success.html
debug/live_last_handoff.html
debug/live_handoff_failure*.json
debug/live_handoff_challenge*.html
```

Latest filenames are convenient for inspection. URL-correlated filenames are required
to match a specific failed request.

## Failure and risk assessment

### AtHome challenge/refarm

**Severity: medium, operational.**

The direct HTTP path is challenged after several successful pages. The browser handoff
can recover the list path, but individual detail requests may still fail at handoff
validation.

Recommended actions:

1. Keep challenge detection fail-closed.
2. Keep request-specific handoff artifacts.
3. Add a preflight check for Chrome availability before any LLM or scrape work.
4. Stop a run immediately on missing-browser setup errors.
5. Monitor handoff success rate by request and page type.
6. Do not scale hosts/IPs to bypass challenges.

### Page-budget partial results

**Severity: low, expected.**

The run stops at ten pages by configuration and exposes the partial status.

### LLM cost and latency

**Severity: medium.**

The run used 13 shortlist batches and completed them successfully. Broad harvests and
larger reasoning budgets can increase latency rapidly.

Recommended actions:

1. Track per-stage prompt, completion, reasoning, and elapsed time.
2. Keep flow detection at low or minimal reasoning.
3. Keep query parsing and shortlisting at low reasoning initially.
4. Use medium reasoning for the final recommender only after quality evidence.
5. Add a bounded continuation policy only for providers/models where continuation is
   explicitly supported.
6. Consider smaller or more compact shortlist projections.

### Unknown listing IDs from LLM output

**Severity: medium, quality monitoring.**

The shortlister validates IDs against the candidate set and discards unknown IDs. Keep
that validation and record the rate as a quality metric.

## Recommended next actions

### Immediate infrastructure

1. Add a repository preflight command.
2. Verify Patchright Chrome before any LLM or scraping work.
3. Add the future container/Railway setup step:

```bash
python -m patchright install chrome
```

4. Fail fast on missing browser binaries instead of repeating the same failure for each
   detail listing.

### Immediate observability

1. Preserve request-specific handoff HTML and failure JSON.
2. Keep complete raw provider response envelopes.
3. Add finish-reason and reasoning-token summaries.
4. Separate latest artifacts from correlated per-request artifacts.

### Pipeline optimization

1. Keep the current safe partial-result behavior.
2. Tune shortlist batch size and compact listing projection.
3. Measure low versus medium reasoning by stage.
4. Add a bounded continuation experiment only after provider-specific validation.
5. Avoid broad live probes with excessive page and candidate budgets while debugging.

## Overall assessment

The core architecture is functioning end to end under real network conditions:

```text
query
-> flow and intent parsing
-> filters
-> 389 harvested listings
-> browser refarm
-> 20 shortlisted records
-> 20 details scraped
-> 5 recommendations
-> reports and SQLite persistence
```

The run is partial because page coverage is capped and one detail request failed, but it
produced a valid report and useful operational evidence. The next major investment
should be preflight/fail-fast setup handling and operational telemetry before new product
features.
