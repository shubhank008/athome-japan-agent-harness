# athome-japan-agent-harness

A conversational CLI agent that finds homes to rent or buy on athome.co.jp from
natural language. You describe what you want; the agent translates it into AtHome
filters, harvests matching listings, shortlists with an LLM, scrapes details, and
returns a ranked, reasoned shortlist with direct links.

## Project status

M0 (project skeleton + hygiene) and M1 (scraper core) are implemented. The package
`src/athome_harness/` contains configuration parsing, pydantic data models, the
scraper abstraction layer (`BaseScraper`, `BlockDetected`, `ProxyProvider`), a
token-bucket rate limiter, and an HTTP DOM adapter with block detection and proxy
rotation. LLM, store, and orchestration are pending.

## Documentation

- [PRD.md](PRD.md) -- project-level product requirements (authoritative for product intent)
- [SPEC.md](SPEC.md) -- project-level technical spec (authoritative for filter map, data models, interfaces)
- [PLAN.md](PLAN.md) -- live project plan, updated after every feature
- [docs/specs/001-athome-home-finder/](docs/specs/001-athome-home-finder/) -- feature 001 spec, implementation plan, and marker contract
- [AGENTS.md](AGENTS.md) -- agent workflow and architecture invariants
