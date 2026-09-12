# AtHome harness runbook

This runbook is for local operators running the AtHome CLI, offline probes,
diagnostic probes, and authorized live checks.

## Safety and prerequisites

Run commands from the repository root. Python 3.12 or newer is required.
Install the pinned dependencies before the first run:

```bash
python -m pip install -r requirements.txt
```

Use a local `.env` copied from `.env.example`. Never commit `.env`, cookies,
session state, proxy URLs, API keys, raw live HTML, or diagnostic archives.
The `debug/`, `dump/`, `.probe-work/`, and `reports/` directories are ignored
by Git. Keep live access authorized and bounded. The scraper detects AtHome
challenge pages and fails closed; do not automate puzzle solving or CAPTCHA
bypass.

Useful environment variables:

| Variable | Purpose | Typical operator value |
|---|---|---|
| `DEBUG` | Enables local debug captures and LLM input/output dumps | `true` for an intentional diagnostic run, otherwise `false` |
| `OPENROUTER_API_KEY` | OpenRouter credential | secret, only for OpenRouter |
| `OPENCODEGO_API_KEY` | OpenCodeGo credential | secret, only for OpenCodeGo |
| `ATHOME_LLM_PROVIDER` | LLM adapter | `openrouter` or `opencodego` |
| `WEBSHARE_PROXY_USER/PASS` | Optional proxy credentials | secret, optional |
| `ATHOME_MAX_PAGES` | Maximum list pages in one search | small value for smoke tests, for example `2` or `10` |
| `ATHOME_HTTP_TIMEOUT_S` | AtHome request timeout | project default or a deliberately bounded override |
| `ATHOME_LLM_TIMEOUT_S` | LLM request timeout | project default; large values can make failures slow |
| `ATHOME_PROXY_RETRIES` | Rehandoff/proxy retry budget | project default |
| `ATHOME_SHORTLIST_SIZE` | Number of shortlist candidates | project default |
| `ATHOME_RECOMMENDATIONS_COUNT` | Final recommendations | project default |
| `ATHOME_STORE_PATH` | SQLite store location | an ignored path such as `.run-data/athome.db` |

Do not put credentials directly in command history. Set them in the ignored
`.env`, or export them in the shell without printing them.

## Which command to run

The operator wording has a strict meaning:

- **"Run live" means run the interactive AtHome CLI**, not the full probe.
- **"Run the full probe" means run `scripts/full_run_probe.py`**.
- Individual probes below are used only when their specific behavior is requested.

## 1. Live AtHome CLI

The CLI is interactive and accepts commands such as `search`, `save N`,
`reject N`, `more like N`, `refine ...`, and `quit`.

Prepare an ignored output directory and point the SQLite store there:

```bash
mkdir -p .run-data
ATHOME_STORE_PATH=.run-data/athome.db \
PYTHONPATH=src python -m athome_harness.cli
```

Then type, for example:

```text
cheap 2LDK in Osaka
quit
```

For a reproducible one-query CLI smoke run, pipe input and capture the log
outside any directory that the application may clean up:

```bash
rm -rf .run-data
mkdir -p .run-data
ATHOME_STORE_PATH=.run-data/athome.db \
PYTHONPATH=src python -m athome_harness.cli \
  <<'EOF' 2>&1 | tee /tmp/athome-cli-live.log
cheap 2LDK in Osaka
quit
EOF
```

For diagnostics, enable debug explicitly:

```bash
rm -rf .run-data debug
mkdir -p .run-data
DEBUG=true ATHOME_STORE_PATH=.run-data/athome.db \
PYTHONPATH=src python -m athome_harness.cli \
  <<'EOF' 2>&1 | tee /tmp/athome-cli-debug.log
cheap 2LDK in Osaka
quit
EOF
```

The CLI writes reports as `report-*.md` and `report-*.json` under the current
`reports/` directory. The SQLite store is written to `ATHOME_STORE_PATH`.
With `DEBUG=true`, additional local diagnostics can include:

- `debug/llm_last_input.json`
- `debug/llm_last_output.json`
- `debug/llm_last_invalid.json` when JSON validation fails after repair
- `debug/detail_last_success.html`
- `debug/detail_last_failure.json`
- `debug/live_last_success.html`, `debug/live_last_handoff.html`, and bounded
  failure metadata from session refarming
- `debug/cookie_handoff_*.json`, `debug/cookies.txt`, and
  `debug/session_state.json` when browser handoff succeeds

These files can contain sensitive session material or large HTML. Inspect them
locally only and do not commit or upload them.

## 2. Full search-run probe

Use this only when the request explicitly says **full probe**. It composes the
full `SearchSession` funnel: query parsing, filter encoding, harvesting,
shortlisting, detail hydration, recommendations, reports, and SQLite storage.

Offline fixture mode is the default and makes no network request:

```bash
PYTHONPATH=src python scripts/full_run_probe.py \
  --mode fixture \
  --query "cheap 2LDK in Osaka"
```

Authorized live mode:

```bash
rm -rf .probe-work
PYTHONPATH=src python scripts/full_run_probe.py \
  --mode live \
  --query "cheap 2LDK in Osaka" \
  --work-dir .probe-work \
  --keep-outputs 2>&1 | tee /tmp/athome-full-probe-live.log
```

The probe removes its work directory on exit unless `--keep-outputs` is used.
It also removes an existing work directory at startup. Therefore, do **not**
redirect stdout/stderr to `.probe-work/live.log`: the probe deletes that file
before the shell can use it. Redirect to `/tmp`, as above, or another path
outside `--work-dir`.

Probe artifacts are normally under `.probe-work/` when kept:

- `reports/report-*.md` and `reports/report-*.json`
- `debug/detail_last_success.html`
- `debug/detail_last_failure.json`

## 3. Single-property rental probe

This checks one list page and its first detail through the production-shaped
HTTP and parser path.

Offline fixture mode:

```bash
rm -rf debug/property-probe-fixture
PYTHONPATH=src python scripts/property_rental_probe.py \
  --input-mode fixture \
  --list-html tests/fixtures/osaka_rental_list.html \
  --detail-html tests/fixtures/detail_1122949022.html \
  --debug-dir debug/property-probe-fixture
```

Authorized network mode:

```bash
PYTHONPATH=src python scripts/property_rental_probe.py \
  --input-mode url \
  --url 'https://www.athome.co.jp/chintai/osaka/list/' \
  --timeout 20 \
  --debug-dir debug/property-probe-live
```

Expected summary artifacts are `property_summary.txt` and
`property_detail.txt` in the selected debug directory. Use
`--help` to see optional input paths and flags.

## 4. LLM provider probe

Offline provider path:

```bash
PYTHONPATH=src python scripts/llm_probe.py \
  --fake \
  --prompt "2 bedrooms near a station in Osaka, rent under 80k"
```

Authorized live provider path, using the provider selected in `.env`:

```bash
PYTHONPATH=src python scripts/llm_probe.py \
  --prompt "2 bedrooms near a station in Osaka, rent under 80k" \
  2>&1 | tee /tmp/athome-llm-live.log
```

Override the configured provider or model only for a deliberate comparison:

```bash
PYTHONPATH=src python scripts/llm_probe.py \
  --provider opencodego \
  --model deepseek-v4-flash \
  --prompt "2 bedrooms near a station in Osaka, rent under 80k"
```

The probe prints provider/model selection, token usage, and parsed output. It
does not print API keys.

## 5. HTTP manual probe

This exercises the HTTP adapter, challenge detection, browser handoff, and
rebound HTTP request. It is a diagnostic live network call, not the CLI:

```bash
PYTHONPATH=src python scripts/http_manual_probe.py \
  --url 'https://www.athome.co.jp/chintai/osaka/list/' \
  --debug-dir debug/http-manual-probe \
  2>&1 | tee /tmp/athome-http-manual.log
```

Optional `--capsolver-key` is supported by the script's interface. Do not use
a solver or attempt to bypass a challenge without separate explicit security
authorization. The normal fail-closed path is preferred.

Possible artifacts include `http_first.html`, `http_second.html`,
`http_first_raw.html`, and `http_second_raw.html`, plus handoff diagnostics
created by the production refarmer. Treat all of them as sensitive live data.

## 6. Patchright browser observation probe

Use this when browser rendering or challenge behavior itself must be observed.
It may create screenshots, HTML, video, trace, event logs, cookies, and session
state. Run it only on an authorized operator machine:

```bash
PYTHONPATH=src python scripts/playwright_manual_probe.py \
  --url 'https://www.athome.co.jp/chintai/osaka/list/' \
  --debug-dir debug/playwright-manual \
  --solve-mode none \
  2>&1 | tee /tmp/athome-playwright-manual.log
```

For a visible browser session when X is unavailable, use the installed virtual
display if present:

```bash
PYTHONPATH=src xvfb-run python scripts/playwright_manual_probe.py \
  --debug-dir debug/playwright-manual \
  --solve-mode none
```

The probe can write `playwright_before.html`, `playwright_after.html`, matching
PNG screenshots when debug capture is enabled by the script, `playwright_events.jsonl`,
`playwright_challenge_trace.zip`, `playwright_challenge.webm`, and
`session_state.json`. Never commit or share these artifacts.

## 7. Live DOM inspection

This fetches one live list page and prints selector counts against the current
parser contract:

```bash
PYTHONPATH=src python scripts/inspect_live_dom.py \
  --url 'https://www.athome.co.jp/chintai/osaka/list/' \
  2>&1 | tee /tmp/athome-live-dom.log
```

If saving HTML is explicitly needed, choose an ignored path and validate that
the response is actual listing content rather than an AtHome challenge page:

```bash
mkdir -p debug/live-dom
PYTHONPATH=src python scripts/inspect_live_dom.py \
  --save-html debug/live-dom/osaka-list.html
```

## 8. Filter-map utility

This is offline and reads/writes the checked-in versioned filter map. Inspect
help first, then use an explicit output path when regenerating intentionally:

```bash
PYTHONPATH=src python tools/dump_filter_map.py --help
```

Do not overwrite `filters/data/filter_map.v1.json` casually. Any intentional
map change requires the corresponding parser/spec/test review.

## 9. Tests and verification

Normal offline checks:

```bash
PYTHONPATH=src python -m pytest
ruff check .
PYTHONPATH=src mypy src tests scripts tools
```

The live integration test is opt-in and requires authorization:

```bash
ATHOME_LIVE_TEST=1 PYTHONPATH=src python -m pytest -m live tests/live/
```

## 10. Reviewing a captured log

Start with stage markers and errors rather than dumping raw HTML or cookies:

```bash
grep -E '\[(SESSION_START|PLAN|HARVEST_DONE|SHORTLIST_DONE|DETAIL_DONE|REPORT|SESSION_END|BUDGET_HIT|PARTIAL_REPORT|DETAIL_FAILED|LLM_CALL|ATHOME_CHALLENGE|REHANDOFF_|CURL_HANDOFF_BOUND)' \
  /tmp/athome-cli-live.log
```

Check generated reports and store paths separately:

```bash
find reports .run-data .probe-work debug -maxdepth 3 -type f -printf '%p %s bytes\n' 2>/dev/null | sort
```

The LLM layer performs exactly one retry only for invalid or schema-mismatched
JSON. A transport failure such as an OpenCodeGo timeout is not converted into a
JSON repair retry and currently propagates as an error from the final
recommendation call. A future transport retry should be added deliberately
with bounded attempts, backoff, and token/cost accounting.
