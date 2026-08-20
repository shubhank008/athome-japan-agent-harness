# athome-japan-agent-harness

A conversational CLI agent that finds homes to rent or buy on athome.co.jp from
natural language. You describe what you want; the agent translates it into AtHome
filters, harvests matching listings, shortlists with an LLM, scrapes details, and
returns a ranked, reasoned shortlist with direct links.

## Project status

M0 (project skeleton + hygiene), M1 (scraper core), M2 (filter map), M3 (parsing),
M4 (LLM layer), M5 (store), M6 (orchestration + CLI), M7 (maintenance
surfaces: US-008 proxy-fallback integration coverage plus quickstart/architecture
documentation), and M8 (config-driven provider layer with OpenAI-compatible base,
OpencodeGo transport, and provider factory) are implemented. The package
`src/athome_harness/` contains configuration parsing, pydantic data models, the scraper
abstraction layer (`BaseScraper`, `BlockDetected`, `ProxyProvider`), a token-bucket rate
limiter, an HTTP DOM adapter with block detection, proxy rotation, and AtHome challenge
handling, list and detail parsers that turn captured HTML into `ListingSummary`/
`ListingDetail` models, a versioned filter-map schema with validation, a SearchPlan
encoder that produces AtHome POST parameters, a weekly-refresh extraction tool with a
checked-in snapshot, the LLM layer: `BaseLLMProvider` with schema-validated completion
and token accounting, OpenAI-compatible base class with OpenRouter and OpencodeGo
transports via curl-cffi, NL query parser with rent/buy
flow resolution and clarification handling, token-bounded batched shortlisting, and
top-Y recommendation ranking with markdown and JSON reports, the persistence layer:
`BaseDataStore` abstract interface with a SQLite backend (`SqliteStore`) for listings,
searches, recommendations, saves, rejects, and cache metadata, the provider factory:
config-driven selection of LLM, store, and scraper adapters from `.env` settings, and
the orchestration
layer: a budget-aware pagination harvester with partial-result behavior, a typed
conversational CLI/REPL with dependency injection and feedback commands (save/reject/
more like/refine), a scripted fixture-based e2e search session, and a `SessionRefarmer`
fallback loop that recovers from an AtHome IP block by farming a fresh browser session
on demand.

## Quickstart

Run one of the CLI commands below and follow the prompts. A single search session
needs an LLM provider key and (optionally) Webshare proxy credentials.

### 1. Environment setup

Copy the environment template and fill in your credentials. The harness fails
loudly at startup on an unknown `ATHOME_`-prefixed key, so keep `.env` and
`.env.example` in sync if you add a knob.

```bash
cp .env.example .env
# Edit .env: set ATHOME_LLM_PROVIDER (default openrouter) and its API key; WEBSHARE_PROXY_USER/PASS are optional.
```

The exact accepted keys and their defaults live in `src/athome_harness/config.py`
and are mirrored in `.env.example`.

### 2. Installation

Python 3.12+ is required. Install the exact-pinned dependencies:

```bash
pip install -r requirements.txt
```

The HTTP path uses curl-cffi with browser impersonation. The optional browser
refarm path uses Patchright; when AtHome blocks your IP, install Google Chrome
on the machine that farms sessions (Patchright drives the installed Chrome
channel).

### 3. First search

Run the interactive CLI:

```bash
PYTHONPATH=src python -m athome_harness.cli
```

Type a natural-language query, for example:

```
cheap 2LDK in Osaka
```

The agent parses the query into AtHome filters, harvests matching listings,
shortlists with the LLM, scrapes details, and writes a ranked report. Within
the same session you can then:

```
save 1        # remember listing 1
reject 2      # hide listing 2 from future runs
more like 1   # find options similar to listing 1
refine near a station   # start a new search with an extended query
```

Reports are written as both `report-*.md` and `report-*.json` under the session
report directory. A scripted, deterministic end-to-end smoke test that runs the
same loop against fixture HTML and a fake LLM (no network) is:

```bash
PYTHONPATH=src python -m pytest tests/e2e/test_search_session.py
```

### 4. Safe live-test expectations

Live extraction talks to athome.co.jp and is not run by the automated suite.
It requires AtHome access authorization and a working LLM key. Before running
anything live, know these boundaries:

- Direct connection is always tried first; Webshare proxies engage only when an
  IP block (403/429 or an AtHome CAPTCHA/puzzle page) is detected.
- AtHome may answer with an HTTP 200 puzzle/authentication page. The harness
  detects it, logs it (redacted), and never parses or saves that page as listing
  data. It never solves a CAPTCHA or bypasses a challenge.
- When blocked, `SessionRefarmer` farms one fresh browser session (bounded) and
  retries; if still blocked it re-raises and the run degrades to a partial
  report rather than emitting wrong data.
- Run only the bounded, opt-in live checks and never enable live network access
  in CI. The automated suite runs against fixtures and fakes only.

```bash
ATHOME_LIVE_TEST=1 pytest -m live tests/live/test_playwright_curl_live.py
```

## Architecture

The harness is organized around abstract interfaces so concrete backends are
drop-in swappable (see AGENTS.md, Abstract First). The funnel is:

```
NL query -> query_parser -> SearchPlan -> filters/encoder -> HARVEST ->
shortlister -> detail scrape -> recommender -> report (md + json) -> sqlite store
```

Key layers in `src/athome_harness/`:

- `scraping/` - `BaseScraper` and `ProxyProvider` contracts; `HttpDomAdapter`
  (curl-cffi) with block/challenge detection and proxy rotation; `list_parser`
  and `detail_parser`; `harvester` (bounded pagination); `SessionRefarmer`
  (direct-first, then farm/rebind recovery); `rate_limiter`; Patchright cookie
  farmer.
- `llm/` - `BaseLLMProvider`, OpenAI-compatible base class with OpenRouter and
  OpencodeGo transports, query parser, shortlister, recommender.
- `filters/` - versioned filter-map schema plus encoder that maps a `SearchPlan`
  to AtHome POST parameters.
- `store/` - `BaseDataStore` with a SQLite backend.
- `config.py`, `models.py` - strict environment parser and pydantic data models.

### Provider and adapter choices

Providers are selected by config, not code (see `src/athome_harness/providers.py`):

- **LLM** (`ATHOME_LLM_PROVIDER`): `openrouter` (default) or `opencodego`. Both share
  the `OpenAICompatibleProvider` base over curl-cffi. The general model defaults to
  `deepseek/deepseek-v4-flash-0731`; OpencodeGo uses `opencode-go/deepseek-v4-flash`
  at its own base URL. Construction fails loudly when the selected provider's API key
  is absent.
- **Scraper** (`ATHOME_SCRAPER_PROVIDER`): `http` (default, the only supported value).
  `HttpDomAdapter` over curl-cffi with block/challenge detection and Webshare proxy
  rotation; on a block it falls back through the production `SessionRefarmer`.
- **Store** (`ATHOME_STORE_PROVIDER`): `sqlite` (default) via `SqliteStore`.

### Pagination and budget behavior

Budgets are configurable through `ATHOME_*` env keys and covered by SPEC section 5:

- Result page size 30, max 100 pages per live search, runtime budget 30 minutes.
- The harvester checks budgets before each page and returns a partial report with an
  explicit `partial: true` marker when it aborts rather than emitting wrong data.
- Rate limit 1 request / 2 s with 0-1 s jitter; HTTP timeout 30 s; proxy retries 3.
- Live searches scrape 100% of the LLM-filtered result set; broad-net freshness
  coverage is delegated to the optional post-MVP prefetch cache, not to live searches.

### LLM repair and token loop

Every schema-validated LLM call goes through `BaseLLMProvider.complete_json`:

- The model is asked for a pydantic-schema-validated JSON answer.
- If the first completion is not schema-valid, exactly one repair retry runs with an
  explicit fix-the-JSON instruction; a second failure raises a typed
  `LLMJSONInvalidError` (contract marker `LLM_JSON_INVALID`).
- Prompt and completion tokens are summed across the original and any repair call
  into the returned `LLMUsage` and reported by the probe.
- Long harvests are scored in token-bounded batches (max ~4000 estimated tokens per
  batch) so prompt size stays bounded regardless of harvest size.

### Mermaid architecture

```mermaid
flowchart LR
    U[Operator / user] --> Q[NL query]
    Q --> P[query_parser]
    P --> SP[SearchPlan]
    SP --> FE[filters / encoder <br/> versioned filter map]
    FE --> H[harvester <br/> pagination + budgets]
    H --> LS[Shortlister <br/> token-bounded batches]
    LS --> D[detail scrape]
    D --> R[Recommender top-Y]
    R --> REP[report md + json]
    REP --> S[(SqliteStore)]

    subgraph LLM[LLM layer: BaseLLMProvider]
        FA[factory: openrouter / opencodego] --> LS
        FA --> P
        FA --> R
    end
    subgraph NET[Scraper layer]
        A[HttpDomAdapter curl-cffi] --> H
        A --> D
        REF[SessionRefarmer] --> A
        PROX[Webshare proxy on block] --> A
    end
```

### Available tools and probes

Operator and diagnostic scripts live in `scripts/` and `tools/`. The three
production-shaped probes are bounded, fail closed on an AtHome challenge, and never
write challenge HTML, cookies, session state, proxy URLs, or credentials to tracked
artifacts. All three are safe to inspect with `--help` and have offline verification
paths that need no live request:

- `scripts/property_rental_probe.py` - one property/listing flow. `--input-mode url`
  (default) fetches a list page then its first detail over the HTTP adapter, bounded by
  `--timeout`; `--input-mode fixture` parses `--list-html` and optional `--detail-html`
  with no network. Artifacts go to `--debug-dir`. Shows request and parse stages and
  validates content before writing.
- `scripts/llm_probe.py` - configured LLM through the real provider factory and the
  schema-validated JSON path. `--provider` / `--model` / `--prompt` select the target;
  `--fake` runs a canned no-network provider. Reports resolved provider/model, token
  usage, and parsed output; fails loudly when credentials are absent and never prints
  a key.
- `scripts/full_run_probe.py` - the full funnel in `SearchSession` order (query parser,
  filter encoder, harvester, shortlister, detail parsing, recommender, report, store).
  Default `--mode fixture` is offline and deterministic; `--mode live` is opt-in and
  builds production transports. Cleans up its `--work-dir` on exit unless
  `--keep-outputs`.

Diagnostic probes (these capture raw artifacts under an ignored `debug/` dir and must
not be committed):

- `scripts/http_manual_probe.py` - raw `HttpDomAdapter` fetch with debug output.
- `scripts/playwright_manual_probe.py` - headed Patchright browser observation.
- `tools/dump_filter_map.py` - dump the versioned filter map.

Authoritative references (keep these in sync with code changes):

- [PRD.md](PRD.md) - project-level product requirements (authoritative for product intent)
- [SPEC.md](SPEC.md) - project-level technical spec (authoritative for filter map, data models, interfaces)
- [PLAN.md](PLAN.md) - live repository-level plan
- [Feature 001 spec](docs/specs/001-athome-home-finder/spec.md) - task-scoped product/user-story spec
- [Feature 001 plan](docs/specs/001-athome-home-finder/plan.md) - milestone/task tracking and invariants
- [Marker contract](docs/specs/001-athome-home-finder/contracts/log-markers.md) - exact log markers and forbidden failure patterns
- [AGENTS.md](AGENTS.md) - agent workflow and architecture invariants
- [Feature 002](docs/specs/002-playwright-cookie-fetcher/) - Playwright browser cookie farmer
- [Feature 003](docs/specs/003-curl-cffi-http-integration/) - curl-cffi HTTP adapter integration
- [Feature 004](docs/specs/004-playwright-challenge-diagnostics/) - browser challenge diagnostics and headed probe
- [Feature 005](docs/specs/005-patchright-runtime/) - Patchright browser runtime migration
- [Feature 006](docs/specs/006-lean-cookie-fetcher/) - lean production cookie fetcher (diagnostics moved to probe-only)

## Browser cookie handoff

When the HTTP worker reports an AtHome WAF challenge, use `SessionRefarmer` to
orchestrate the automatic retry loop. It tries the cheap curl-cffi path first;
on block, it farms a fresh browser session via `PlaywrightCookieFetcher`, persists
the handoff, and retries the request:

```bash
pip install -r requirements.txt
# Patchright uses the locally installed Chrome channel.
# Install Google Chrome separately if it is not already available.
```

```python
from athome_harness.scraping import SessionRefarmer, HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.config import Budgets

async def fetch_with_refarm(url: str, proxy_url: str | None = None) -> str:
    def build_adapter(handoff):
        return HttpDomAdapter(Budgets(), handoff=handoff)

    async def farm():
        return await PlaywrightCookieFetcher(proxy_url=proxy_url).farm()

    refarmer = SessionRefarmer(build_adapter=build_adapter, farm=farm)
    return await refarmer.fetch_html(url)
```

The default curl-cffi profile is `chrome`; `safari_ios` is also supported for
non-browser sessions through `HttpDomAdapter(..., impersonate="safari_ios")`.
A handoff always uses the profile recorded when it was farmed. Run the bounded
live integration check only when AtHome access is authorized:

```bash
ATHOME_LIVE_TEST=1 pytest -m live tests/live/test_playwright_curl_live.py
```

The farmer uses one headless Patchright persistent context with the installed Chrome
channel and the existing stealth compatibility hook, waits three seconds after render,
and persists only the handoff (`cookie_handoff_<proxy>.json`, `cookies.txt`) and a
`session_state.json` snapshot. It performs at most one visible press-hold verification
click and never drags a puzzle piece. Screenshots, browser trace, video, and event
log are captured only by the operator probe (see below), not the production farmer.
The handoff is bound to the same proxy and user agent; workers must refarm when
curl-cffi is blocked again. `debug/` is ignored because it contains cookies and
session data.

For an operator-driven headed observation, run this locally on the machine whose
IP and browser window you want to inspect. The command pauses after the initial
three-second render; interact manually in the browser, then press Enter in the
terminal to capture the after state:

```bash
PYTHONPATH=src python scripts/playwright_manual_probe.py  # Patchright + Chrome
# Optional: --proxy http://user:password@host:port --url https://www.athome.co.jp/chintai/osaka/list/
```

The probe writes `playwright_before.html/.png`, `playwright_after.html/.png`,
`playwright_challenge.webm`, `playwright_challenge_trace.zip`, and
`playwright_events.jsonl` under `debug/`. The script name and artifact names remain
backward-compatible even though the runtime is Patchright. Do not commit or share
these artifacts: they can contain session cookies and private page data.
