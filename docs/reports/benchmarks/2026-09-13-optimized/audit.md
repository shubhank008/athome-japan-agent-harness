# Optimized live probe audit report

## Run

```text
query: cheap 1K in Osaka
provider: OpenCodeGo
model: GLM-5.2
reasoning effort: local .env value
max token budget: local .env value
work directory: /tmp/athome-full-probe-live-optimized
```

## Outcome

```text
status: partial
results_seen: 379
pages_scraped: 10
shortlist: 20
shortlist batches: 6
shortlist failures: 0
details scraped: 20
detail failures: 1
recommendations: 5
```

Reports:

```text
/tmp/athome-full-probe-live-optimized/reports/report-809679ad-fe74-4f30-a68e-a6e37cb9e207.md
/tmp/athome-full-probe-live-optimized/reports/report-809679ad-fe74-4f30-a6e37cb9e207.json
```

## Flow analysis

Query parsing completed without clarification. Osaka was treated as a broad rental scope,
1K was treated as a hard layout filter, and cheap rent was used as a soft preference.
The filter encoder reported no unmapped parameters.

AtHome returned a challenge after initial direct requests. Browser refarm succeeded and
the rebound HTTP session continued harvesting through the ten-page configured limit.

The compact projection reduced 379 candidates to six shortlist batches. All six batches
succeeded. Aggregate shortlist usage was:

```text
prompt tokens: 52,283
completion tokens: 27,392
stage total: 86,571
elapsed: approximately 197 seconds
```

Twenty detail targets were attempted. Nineteen completed through direct or rebound HTTP;
one encountered challenge/handoff rejection. The final recommender completed with five
recommendations. Its final provider response reported:

```text
prompt tokens: 9,274
completion tokens: 1,837
reasoning tokens: 624
finish_reason: stop
```

Cumulative SQLite counts after the run were:

```text
listings:        1,787
agencies:           32
hydration_jobs:  1,040
searches:            8
recommendations:    18
```

These counts include prior local runs. `athome.db` remains untracked local evidence.

## Optimization comparison

The earlier medium benchmark used 13 shortlist batches and approximately 192 seconds.
This optimized run used six batches and approximately 197 seconds. The projection reduced
batch count substantially, but wall time did not improve in this sample because provider
latency and reasoning time dominate larger batches.

The recommender prompt still measured approximately 116,079 characters in the optimized
capture. The saved schema analysis identifies the remaining retained fields and their
serialized sizes. The largest known waste from the previous capture, repeated full agency
objects, is no longer present in the compact projection.

## Building diversity

Final recommendations remain unit-level and may contain sibling units from the same
building. Building -> Property[] aggregation and report-time building diversification
remain deferred. Internal records should remain unit-level; a future frontend can show
sibling units from the database outside the LLM result set.

## Risks and recommendations

1. Add preflight before LLM calls to verify Patchright Chrome and writable runtime paths.
2. Stop immediately on browser-not-found setup errors.
3. Keep correlated handoff HTML/failure artifacts and raw provider envelopes in DEBUG mode.
4. Continue measuring prompt size, reasoning tokens, visible output, latency, and ranking
   quality together.
5. Investigate remaining large fields from the optimized schema table, especially bounded
   narrative and feature fields.
6. Consider smaller shortlist batches if provider latency remains high.
7. Track unknown listing IDs and same-building recommendation repetition as quality metrics.
8. Keep T35 and T36 deferred until the integrated rental architecture and prompt contract
   have stabilized.

## Assessment

The optimized pipeline completed all ranking and recommendation stages with zero shortlist
JSON failures and five final recommendations. The run remains partial because of the
configured page limit and one detail challenge failure. Compact projection reduced
shortlist batch count from 13 to 6, but this sample did not demonstrate wall-clock gain.
The next highest-value work is prompt-size measurement/trimming, preflight/fail-fast setup,
and quality evaluation of unit versus building-level presentation.
