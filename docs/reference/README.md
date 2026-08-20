# Building-block reference

In-depth, API-spec-style documentation for every building block in the harness:
what each class and function does, its parameters and fields with types, and how
the blocks compose into the production search funnel. This directory is the
authoritative companion to the inline docstrings; it exists so a new developer
(or an operator) can understand the system without reading every source file.

Per the repository documentation rule (see `AGENTS.md`), every building block
must have a reference page here, and every page must cross-link to the blocks it
depends on and the blocks that depend on it. When you add or change a building
block, update its page in the same change.

## Reading order

Start with the [architecture and funnel](architecture.md) for the end-to-end
shape, then drill into the layer you care about. Each page lists its
dependencies and dependents at the top.

## Pages

| Page | Building blocks | Layer |
|------|-----------------|-------|
| [architecture.md](architecture.md) | `SearchSession`, `SessionDeps`, the funnel | Orchestration |
| [data-models.md](data-models.md) | `SearchPlan`, `ListingSummary`, `ListingDetail`, `PriceBreakdown`, `Recommendation`, `FilterMap`, `RunReport` | Contract |
| [config.md](config.md) | `Settings`, `Budgets`, env keys | Configuration |
| [llm.md](llm.md) | `BaseLLMProvider`, `OpenAICompatibleProvider`, `OpenRouterProvider`, `OpenCodeGoProvider`, `QueryParser`, `Shortlister`, `Recommender` | LLM |
| [filters.md](filters.md) | `FilterMap` schema, `FieldCondition`, `encode` | Filters |
| [scraping.md](scraping.md) | `BaseScraper`, `HttpDomAdapter`, `PlaywrightCookieFetcher`, `SessionRefarmer`, `Harvester`, `CookieHandoff`, `ProxyProvider`, `WebshareProxyProvider` | Scraping |
| [parsers.md](parsers.md) | `parse_list_page`, `parse_detail_page`, DOM access map | Parsing |
| [store.md](store.md) | `BaseDataStore`, `SqliteStore` | Persistence |
| [providers.md](providers.md) | `build_llm_provider`, `build_store`, `build_production_fetch`, `build_proxy_provider` | Factory |
| [probes.md](probes.md) | `property_rental_probe`, `llm_probe`, `full_run_probe`, `probe_common` | Operator tooling |

## Conventions used in these pages

* **Type** column entries use the exact annotation from the source.
* **Default** column entries are the value used when the caller omits the
  argument; `required` means there is no default.
* Numeric defaults (budgets, timeouts, retry budgets) are the authoritative
  values from `SPEC.md` section 5 and `config.py`; the two must never drift.
* Mermaid diagrams show the real call order, not an idealized one.
