"""Provider factory: builds concrete Interface-Adapter implementations from Settings.

The harness talks to external systems exclusively through interfaces
(:class:`BaseLLMProvider`, :class:`BaseDataStore`, :class:`BaseScraper`). This
module is the single place that decides *which* concrete adapter to build based
on runtime configuration (:class:`Settings` from ``.env``), so switching or
testing a provider is a config change rather than a code change.

Each ``build_*`` helper validates the relevant ``Settings`` selector and raises
:class:`ValueError` for an unknown value, keeping :class:`Settings` itself
permissive (see ``config.py``) while enforcing valid identifiers here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from athome_harness.config import (
    LLM_PROVIDER_OPENCODEGO,
    LLM_PROVIDER_OPENROUTER,
    SCRAPER_PROVIDER_HTTP,
    STORE_PROVIDER_SQLITE,
    Budgets,
    Settings,
)
from athome_harness.llm.base import BaseLLMProvider
from athome_harness.scraping.base import ProxyProvider
from athome_harness.store.base import BaseDataStore

logger = logging.getLogger(__name__)


def build_proxy_provider(settings: Settings, budgets: Budgets) -> ProxyProvider | None:
    """Build the rotating proxy provider, or ``None`` when no proxy is configured.

    Webshare credentials are optional in :class:`Settings`. When either
    ``WEBSHARE_PROXY_USER`` or ``WEBSHARE_PROXY_PASS`` is unset this returns
    ``None`` so the HTTP adapter runs direct-only (no proxy rotation) instead of
    failing at construction. When both are set it returns the Webshare provider.
    """
    if not settings.webshare_proxy_user or not settings.webshare_proxy_pass:
        logger.info(
            "[PROXY_DISABLED] reason=no-credentials; direct connection only (no proxy rotation)"
        )
        return None
    from athome_harness.scraping.proxy.webshare import WebshareProxyProvider

    return WebshareProxyProvider(settings=settings, budgets=budgets)


def build_llm_provider(settings: Settings) -> BaseLLMProvider:
    """Build the configured LLM transport (OpenRouter or OpencodeGo)."""
    provider = settings.llm_provider.strip().lower()
    if provider == LLM_PROVIDER_OPENROUTER:
        from athome_harness.llm.openrouter import OpenRouterProvider

        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=settings.general_model,
            max_tokens=settings.llm_max_tokens,
        )
    if provider == LLM_PROVIDER_OPENCODEGO:
        from athome_harness.llm.opencodego import OpenCodeGoProvider

        return OpenCodeGoProvider(
            api_key=settings.opencodego_api_key,
            model=settings.opencodego_model,
            base_url=settings.opencodego_base_url,
            max_tokens=settings.llm_max_tokens,
        )
    raise ValueError(
        f"Unknown LLM provider '{settings.llm_provider}'. "
        f"Expected one of: {LLM_PROVIDER_OPENROUTER}, {LLM_PROVIDER_OPENCODEGO}."
    )


def build_store(settings: Settings) -> BaseDataStore:
    """Build the configured persistence backend (currently SQLite)."""
    provider = settings.store_provider.strip().lower()
    if provider == STORE_PROVIDER_SQLITE:
        from athome_harness.store.sqlite_store import SqliteStore

        return SqliteStore(settings.store_path)
    raise ValueError(
        f"Unknown store provider '{settings.store_provider}'. "
        f"Expected one of: {STORE_PROVIDER_SQLITE}."
    )


def build_production_fetch(
    budgets: Budgets | None = None, settings: Settings | None = None
) -> Callable[[str], str]:
    """Build a production page fetch callable over the SessionRefarmer fallback.

    Selects the scraper adapter named by ``settings.scraper_provider`` (only
    ``http`` is currently supported) and constructs the async refarm loop
    (curl-cffi direct, then Patchright session on block), exposing it as a
    synchronous URL -> HTML callable. This path performs live network I/O and is
    intended for human use only; tests and the scripted e2e run inject fakes.
    """
    from athome_harness.scraping.cookie_handoff import CookieHandoff
    from athome_harness.scraping.http_adapter import HttpDomAdapter
    from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
    from athome_harness.scraping.session_refarmer import SessionRefarmer

    if settings is None:
        settings = load_settings()
    budgets = budgets or settings.budgets

    provider = settings.scraper_provider.strip().lower()
    if provider != SCRAPER_PROVIDER_HTTP:
        raise ValueError(
            f"Unknown scraper provider '{settings.scraper_provider}'. "
            f"Expected one of: {SCRAPER_PROVIDER_HTTP}."
        )
    # Proxy rotation is optional: with no Webshare credentials the adapter runs
    # direct-only and a block surfaces immediately instead of rotating.
    proxy = build_proxy_provider(settings, budgets)

    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
        return HttpDomAdapter(
            budgets=budgets,
            proxy_provider=proxy,
            handoff=handoff,
        )

    refarmer = SessionRefarmer(build_adapter=build_adapter, farm=PlaywrightCookieFetcher().farm)

    def fetch(url: str) -> str:
        return asyncio.run(refarmer.fetch_html(url))

    return fetch


def load_settings() -> Settings:
    """Build runtime ``Settings`` from the environment.

    ``openrouter_api_key`` is typed as required, so pass it explicitly; the
    value comes from the ``OPENROUTER_API_KEY`` process environment variable
    (empty when unset, in which case downstream LLM calls fail loudly).
    """
    import os

    return Settings(openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""))
