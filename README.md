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
```

The farmer uses one headless Chromium instance, waits three seconds after render,
and captures challenge HTML/screenshots under `debug/` when the `Click to verify`
flow appears. It performs at most one visible verification click and never drags a
puzzle piece. The handoff is bound to the same proxy and user agent; workers must
refarm when curl-cffi is blocked again. `debug/` is ignored because it contains
cookies and browser captures.
