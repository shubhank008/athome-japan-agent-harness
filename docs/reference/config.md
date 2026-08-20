# Configuration

The configuration contract in `src/athome_harness/config.py`: the `Budgets`
value object, the `Settings` environment parser, and the accepted env-key
registry. SPEC section 5 is the product-level source of truth for the numeric
defaults; this page is the field-by-field reference, and the two must never
drift.

* **Depends on:** nothing in the package (leaf module).
* **Depended on by:** every layer via the [providers factory](providers.md)
  (`build_production_fetch`, `build_llm_provider`, `build_store`) and the
  operator probes.

## Budgets

The pydantic value object carrying the budget knobs. Every field is `ge=0`
(`llm_max_tokens` is `ge=1`) and has a default.

| Field | Type | Default | Env alias | Meaning |
|-------|------|---------|-----------|---------|
| `rate_requests` | `int` | `1` | `ATHOME_RATE_REQUESTS` | Requests allowed per interval. |
| `rate_interval_s` | `float` | `2.0` | `ATHOME_RATE_INTERVAL_S` | Rate interval in seconds. |
| `rate_jitter_max_s` | `float` | `1.0` | `ATHOME_RATE_JITTER_MAX_S` | Max jitter added per request. |
| `results_per_page` | `int` | `30` | `ATHOME_RESULTS_PER_PAGE` | Expected result count per page. |
| `shortlist_size` | `int` | `20` | `ATHOME_SHORTLIST_SIZE` | Top-X shortlist size. |
| `recommendations_count` | `int` | `5` | `ATHOME_RECOMMENDATIONS_COUNT` | Top-Y recommendations. |
| `max_pages` | `int` | `100` | `ATHOME_MAX_PAGES` | Page budget for one harvest. |
| `runtime_minutes` | `int` | `30` | `ATHOME_RUNTIME_MINUTES` | Runtime budget in minutes. |
| `http_timeout_s` | `float` | `30.0` | `ATHOME_HTTP_TIMEOUT_S` | Per-request HTTP timeout. |
| `proxy_retries` | `int` | `3` | `ATHOME_PROXY_RETRIES` | Proxy rotation budget. |
| `prefetch_ttl_hours` | `float` | `48.0` | `ATHOME_PREFETCH_TTL_HOURS` | Prefetch cache TTL (post-MVP feature, not scheduled). |
| `llm_temperature` | `float` | `0.0` | `ATHOME_LLM_TEMPERATURE` | LLM scoring temperature. |
| `llm_max_tokens` | `int` | `2048` | `ATHOME_LLM_MAX_TOKENS` | LLM completion ceiling. |

## Settings

`Settings(BaseSettings)` loads the `.env` file at the repo root plus the real
environment (real env wins). Only `openrouter_api_key` is required. The
`_reject_unknown_athome_keys` model validator fails loudly on any
`ATHOME_*`-prefixed key that is not a known field, so typos and drift between
`.env.example` and the parser surface immediately.

| Field | Type | Default | Env alias | Meaning |
|-------|------|---------|-----------|---------|
| `openrouter_api_key` | `str` | required | `OPENROUTER_API_KEY` | OpenRouter API key (required). |
| `webshare_proxy_user` | `str \| None` | `None` | `WEBSHARE_PROXY_USER` | Webshare proxy user (optional). |
| `webshare_proxy_pass` | `str \| None` | `None` | `WEBSHARE_PROXY_PASS` | Webshare proxy password (optional). |
| `llm_provider` | `str` | `"openrouter"` | `ATHOME_LLM_PROVIDER` | LLM transport: `openrouter` or `opencodego`. |
| `store_provider` | `str` | `"sqlite"` | `ATHOME_STORE_PROVIDER` | Store backend: `sqlite`. |
| `scraper_provider` | `str` | `"http"` | `ATHOME_SCRAPER_PROVIDER` | Scraper backend: `http`. |
| `opencodego_api_key` | `str \| None` | `None` | `ATHOME_OPENCODEGO_API_KEY` | OpencodeGo API key. |
| `opencodego_model` | `str` | `"opencode-go/deepseek-v4-flash"` | `ATHOME_OPENCODEGO_MODEL` | OpencodeGo model. |
| `opencodego_base_url` | `str` | `".../v1/chat/completions"` | `ATHOME_OPENCODEGO_BASE_URL` | OpencodeGo endpoint. |
| `store_path` | `str` | `"athome.db"` | `ATHOME_STORE_PATH` | SQLite database path. |
| `general_model` | `str` | `"deepseek/deepseek-v4-flash-0731"` | `ATHOME_GENERAL_MODEL` | OpenRouter completion model. |
| `vision_model` | `str` | `"google/gemma-4-31b-it"` | `ATHOME_VISION_MODEL` | OpenRouter vision model (reserved). |
| Budgets fields | see [Budgets](#budgets) | see Budgets | `ATHOME_*` | Every Budgets field is also a Settings field with the same default and alias. |

Model defaults live in constants (`DEFAULT_GENERAL_MODEL`,
`DEFAULT_VISION_MODEL`, `DEFAULT_OPENCODEGO_MODEL`, `DEFAULT_OPENCODEGO_URL`)
so the factory and docs reference one symbol.

## Credential semantics

* `openrouter_api_key` is required even when the configured provider is
  `opencodego`, because the OpenRouter key is the harness's boot credential.
  Tests must pass an explicit throwaway key when constructing `Settings`
  directly.
* Webshare credentials are **optional**: when either is unset,
  [`build_proxy_provider`](providers.md) returns `None` and the production fetch
  runs direct-only instead of raising at construction. This is the
  proxy-optional behavior introduced after PR #16.
* Provider-specific keys (`opencodego_api_key`) are only consulted by the
  transport that uses them; the [llm probe](probes.md) fails clearly when the
  requested provider's credential is absent rather than silently using another
  secret.

## .env.example

`.env.example` is the accepted-key template. The repository invariant is that
runtime configuration changes must update `.env.example` in the same change,
and the `_reject_unknown_athome_keys` validator guarantees the template and the
parser stay in sync in both directions.
