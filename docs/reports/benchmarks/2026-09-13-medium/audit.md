# Low versus medium reasoning benchmark audit

## Run identity

This report covers the medium-effort run on 2026-09-13 using:

```text
query: cheap 1K in Osaka
provider: OpenCodeGo
model: glm-5.2
reasoning effort: medium
max token budget: local .env value at run time
```

The run produced:

```text
status: partial
results_seen: 385
pages_scraped: 10
shortlist: 20
recommendations: 5
detail scraped: 20
detail failed: 0
```

Final reports:

```text
/tmp/athome-full-probe-live-medium-benchmark/reports/report-c2781333-8e74-4601-99f7-41bec06c9697.md
/tmp/athome-full-probe-live-medium-benchmark/reports/report-c2781333-8e74-4601-99f7-41bec06c9697.json
```

## Comparison with low-effort baseline

The low-effort baseline is preserved under:

```text
docs/reports/benchmarks/2026-09-13-low/
```

| Metric | Low run | Medium run | Observation |
|---|---:|---:|---|
| Harvested listings | 389 | 385 | Comparable broad coverage. |
| Harvest pages | 10 | 10 | Same page budget. |
| Shortlist size | 20 | 20 | Same configured shortlist. |
| Shortlist batches | 13 | 13 | Comparable candidate volume. |
| Shortlist failures | 0 | 0 | Both completed all batches. |
| Detail scraped | 20 | 20 | Medium completed all detail targets. |
| Detail failures | 1 | 0 | Medium run avoided the low run's detail failure. |
| Recommendations | 5 | 5 | Same final recommendation count. |
| Shortlist elapsed time | ~161 seconds | ~192 seconds | Medium was slower in this capture. |
| Final report | produced | produced | Both produced reports. |

The low run's exact aggregate shortlist usage was approximately:

```text
prompt: 108,117
completion: 18,502
```

The medium run's exact aggregate shortlist usage was reported in the live log and
should be read alongside the numbered provider envelopes. Individual medium responses
completed with `finish_reason=stop`; no empty-content length failure occurred in this
run.

## Medium-run flow

### Query interpretation

The parser accepted the same broad query without clarification:

```text
flow: rent
Osaka: broad prefecture scope
1K: hard layout filter
cheap: soft low-rent preference
```

### Harvest

The run reached ten pages and 385 listings. AtHome challenge detection and a browser
handoff occurred during harvesting. The run continued through the rebound HTTP path and
stopped at the configured page budget.

### Shortlisting

All thirteen shortlist batches succeeded:

```text
successful batches: 13
failed batches: 0
shortlist: 20
```

### Detail stage

All twenty detail targets completed:

```text
scraped: 20
failed: 0
```

This is better than the low baseline, where one detail target failed. The difference is
not sufficient to attribute causally to reasoning effort because AtHome challenge state
and handoff timing vary between runs.

### Recommendation stage

The final recommender completed with five recommendations. The latest provider response
was:

```text
prompt_tokens: 56,636
completion_tokens: 1,768
reasoning_tokens: 544
finish_reason: stop
```

The recommender prompt is very large and should be reduced independently of reasoning
choice.

## Recommender prompt schema findings

The low-run recommender input contained 20 listing objects and approximately 86,285
user-prompt characters.

The largest field was:

```text
agency: approximately 56,270 characters across 19 listings
```

That is about 65% of the prompt. The full agency entity should not be included in the
ranking prompt. It belongs in persistence and final user-facing reporting.

Strong prompt-trimming candidates:

1. Remove full `agency` objects. Keep at most agency name and access text, or omit them.
2. Remove `detail_fetched_at` and `detail_fresh_until`; these are cache metadata.
3. Remove `url`; retain it for the final report, not reasoning.
4. Remove `internal_id` only if a separate compact listing ID is retained for output mapping.
5. Remove duplicate raw fields such as `age_raw` when `age_display` or normalized age is present.
6. Bound `description` and `remarks` to a fixed character limit.
7. Keep `facility_features`, `pickup_features`, `probable_negatives`, price, area, layout,
   station, walking time, address, building facts, and title.
8. Omit empty fields entirely.

The first prompt-reduction experiment should compare the current full detail projection
against a compact ranking projection. This should happen before increasing reasoning
further.

## Failure and risk assessment

### AtHome challenge/refarm

The challenge/refarm path remains operational but variable. It can recover the list path
and detail path, but individual requests can still fail depending on WAF state and browser
handoff timing.

The environment must have Chrome installed for Patchright. Missing Chrome must be treated
as a setup failure and terminate the run rather than repeating a failed refarm for every
listing.

### Reasoning and token budget

The medium run did not show an empty-content `finish_reason=length` failure. However, this
does not prove that medium enforces a numeric reasoning limit. `reasoning_effort` remains a
qualitative control. The combined completion ceiling still includes reasoning and visible
output.

The correct monitoring fields are:

```text
reasoning_effort requested
max_tokens requested
max_completion_tokens requested
prompt_tokens returned
completion_tokens returned
reasoning_tokens returned
finish_reason returned
visible content length
reasoning content length
```

### Data quality

The final recommendations contained repeated or similar listing titles in the live
capture. The shortlister also has safeguards against unknown listing IDs, but ranking
quality should be evaluated separately from transport success.

## Recommended next actions for an external auditor

### Immediate

1. Review low and medium final reports side by side.
2. Review the raw provider response envelopes for shortlist and recommender calls.
3. Compare recommendation overlap, price ordering, and rationale quality.
4. Review the 56k-character recommender prompt and confirm which fields are actually used.
5. Add a compact ranking projection experiment.

### Infrastructure

1. Add a preflight that verifies Patchright Chrome before LLM calls.
2. Add fail-fast handling for browser-not-found exceptions.
3. Keep request-correlated handoff artifacts.
4. Keep full raw LLM response envelopes during DEBUG runs.

### LLM policy

1. Keep flow detection at low reasoning or disable reasoning if supported.
2. Keep query parsing at low or medium based on ambiguity quality evidence.
3. Keep shortlisting at low initially because it is the highest-volume stage.
4. Evaluate medium mainly for the final recommender.
5. Do not infer a numeric reasoning budget from qualitative `reasoning_effort`.
6. Test bounded continuation only after prompt reduction and provider behavior are stable.

## Overall assessment

The medium run is operationally stronger than the low baseline in this capture:

```text
no shortlist batch failures
all 20 detail targets completed
five recommendations produced
```

But the comparison is not a controlled scientific benchmark because network challenge state,
listing results, provider caching, and model randomness vary. The clearest actionable
finding is independent of low versus medium reasoning: the recommender prompt is dominated
by repeated agency payloads and should be compacted before further reasoning optimization.
