# athome-japan-agent-harness

A conversational CLI agent that finds homes to rent or buy on athome.co.jp from
natural language. You describe what you want; the agent translates it into AtHome
filters, harvests matching listings, shortlists with an LLM, scrapes details, and
returns a ranked, reasoned shortlist with direct links.

## Project status

M0 (project skeleton + hygiene), M1 (scraper core), M2 (filter map), and M3 (parsing)
are implemented. The package `src/athome_harness/` contains configuration parsing,
pydantic data models, the scraper abstraction layer (`BaseScraper`, `BlockDetected`,
`ProxyProvider`), a token-bucket rate limiter, an HTTP DOM adapter with block detection,
proxy rotation, and AtHome challenge handling, list and detail parsers that turn
captured HTML into `ListingSummary`/`ListingDetail` models, a versioned filter-map
schema with validation, a SearchPlan encoder that produces AtHome POST parameters, and
a weekly-refresh extraction tool with a checked-in snapshot. LLM, store, and
orchestration are pending.

## Documentation

- [PRD.md](PRD.md) -- project-level product requirements (authoritative for product intent)
- [SPEC.md](SPEC.md) -- project-level technical spec (authoritative for filter map, data models, interfaces)
- [PLAN.md](PLAN.md) -- live project plan, updated after every feature
- [docs/specs/001-athome-home-finder/](docs/specs/001-athome-home-finder/) -- feature 001 spec, implementation plan, and marker contract
- [docs/specs/002-playwright-cookie-fetcher/](docs/specs/002-playwright-cookie-fetcher/) -- feature 002 Playwright browser cookie farmer
- [docs/specs/003-curl-cffi-http-integration/](docs/specs/003-curl-cffi-http-integration/) -- feature 003 curl-cffi HTTP adapter integration
- [AGENTS.md](AGENTS.md) -- agent workflow and architecture invariants

## Browser cookie handoff

When the HTTP worker reports an AtHome WAF challenge, farm a short-lived browser
session once and pass its handoff to curl-cffi workers:

```bash
pip install -r requirements.txt
playwright install chromium
```

```python
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from curl_cffi import requests

handoff = await PlaywrightCookieFetcher(proxy_url=proxy_url).farm()
response = requests.get(target_url, **handoff.to_curl_cffi_kwargs())

# Or bind the handoff to the scraper used by the workers.
from athome_harness.config import Budgets
from athome_harness.scraping.http_adapter import HttpDomAdapter

scraper = HttpDomAdapter(Budgets(), handoff=handoff)
html = scraper.fetch_html(target_url)
scraper.close()
```

The default curl-cffi profile is `chrome`; `safari_ios` is also supported for
non-browser sessions through `HttpDomAdapter(..., impersonate="safari_ios")`.
A handoff always uses the profile recorded when it was farmed. Run the bounded
live integration check only when AtHome access is authorized:

```bash
ATHOME_LIVE_TEST=1 pytest -m live tests/live/test_playwright_curl_live.py
```

The farmer uses one headless Chromium instance, waits three seconds after render,
and captures challenge HTML/screenshots under `debug/` when the `Click to verify`
flow appears. It performs at most one visible press-hold verification click and
never drags a puzzle piece. Diagnostics also write a Playwright trace, WebM video,
and redacted JSONL event log to `debug/`, including evidence when the challenge
remains blocked. The handoff is bound to the same proxy and user agent; workers
must refarm when curl-cffi is blocked again. `debug/` is ignored because it
contains cookies and browser captures.

For an operator-driven headed observation, run this locally on the machine whose
IP and browser window you want to inspect. The command pauses after the initial
three-second render; interact manually in the browser, then press Enter in the
terminal to capture the after state:

```bash
PYTHONPATH=src python scripts/playwright_manual_probe.py
# Optional: --proxy http://user:password@host:port --url https://www.athome.co.jp/chintai/osaka/list/
```

The probe writes `playwright_before.html/.png`, `playwright_after.html/.png`,
`playwright_challenge.webm`, `playwright_challenge_trace.zip`, and
`playwright_events.jsonl` under `debug/`. Do not commit or share these artifacts:
they can contain session cookies and private page data.
