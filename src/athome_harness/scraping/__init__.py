"""AtHome scraping layer: adapters, session state, and the fetch orchestrator.

Import surface rules for the rest of the harness:

* Production callers fetch pages through :class:`SessionRefarmer`, the
  orchestrator that runs the cheap curl-cffi path first and farms a fresh
  Patchright browser session only when AtHome answers with a challenge
  (``HttpDom -> block -> PlaywrightCookie -> handoff -> HttpDom``).
* The concrete adapters (:class:`HttpDomAdapter`,
  :class:`PlaywrightCookieFetcher`) are single-purpose building blocks.
  Importing them directly is reserved for targeted unit tests and the
  operator probe, where one layer is exercised in isolation.
"""

from athome_harness.scraping.base import BaseScraper, BlockDetected, ProxyProvider, redact_url
from athome_harness.scraping.cookie_handoff import CookieHandoff, proxy_identity
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.scraping.session_refarmer import SessionFarmer, SessionRefarmer
from athome_harness.scraping.session_state import (
    SessionState,
    build_launch_options,
    get_installed_chrome_version,
)

__all__ = [
    "BaseScraper",
    "BlockDetected",
    "CookieHandoff",
    "HttpDomAdapter",
    "PlaywrightCookieFetcher",
    "ProxyProvider",
    "SessionFarmer",
    "SessionRefarmer",
    "SessionState",
    "build_launch_options",
    "get_installed_chrome_version",
    "proxy_identity",
    "redact_url",
]
