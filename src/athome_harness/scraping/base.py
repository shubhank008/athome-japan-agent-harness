"""Scraper abstractions for the AtHome harness (Abstract First).

This module defines the business-facing contract for every scraper backend
(:class:`BaseScraper`), the typed block signal (:class:`BlockDetected`), the URL
redaction helper used by the marker contract, and the :class:`ProxyProvider`
protocol that the HTTP adapter depends on when an IP block is detected.

Per the repository Abstract First invariant, this module imports only the
standard library and other project interfaces. No third-party HTTP or scraping
library is imported here; adapters ``http_adapter.py`` and
``playwright_adapter.py`` provide the concrete implementations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# The one ``signature`` a :class:`BlockDetected` may carry. Mirrors SPEC.md
# section 4 ("signature: 403/429/captcha").
BlockSignature = Literal["403", "429", "captcha"]


def redact_url(url: str) -> str:
    """Strip credentials and query string from a URL so it is safe to log.

    The marker contract forbids logging full URLs with query strings and forbids
    leaking proxy credentials (``PROXY_CREDENTIALS_IN_URL_LOG``). This keeps only
    the scheme, host, and path.
    """
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        # Drop any ``user:pass@`` userinfo prefix before the host.
        netloc = netloc.split("@", 1)[1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


class BlockDetected(Exception):
    """Raised when the target site blocks a request (403, 429, or captcha).

    Carries the detection ``signature`` and the redacted request URL so callers
    and the marker contract can report which kind of block happened without
    leaking credentials or query parameters. The exception message and the log
    record both emit the ``[BLOCK_DETECTED]`` marker verbatim.
    """

    def __init__(self, url: str, signature: BlockSignature) -> None:
        self.url = url
        self.redacted_url = redact_url(url)
        self.signature = signature
        marker = f"[BLOCK_DETECTED] url=<{self.redacted_url}> signature=<{signature}>"
        logger.warning("%s", marker)
        super().__init__(marker)


class BaseScraper(ABC):
    """Abstract scraper contract implemented by HTTP DOM and Playwright backends.

    ``fetch_html`` returns the raw HTML of a page as text; ``fetch_binary``
    returns arbitrary bytes (e.g. images or file downloads). Adapters add their
    own DOM parsing helpers on top. Both methods must raise
    :class:`BlockDetected` when the site blocks the request.
    """

    @abstractmethod
    def fetch_html(self, url: str) -> str:
        """Fetch ``url`` and return its HTML source as text."""

    @abstractmethod
    def fetch_binary(self, url: str) -> bytes:
        """Fetch ``url`` and return the response body as raw bytes."""


class ProxyProvider(Protocol):
    """Minimal proxy-rotation interface the HTTP adapter depends on.

    Implementations begin on a direct connection (``get_proxy`` returns ``None``
    until a block is reported) and engage a proxy only on demand. Concrete
    proxies live under ``scraping/proxy/``.
    """

    def get_proxy(self) -> str | None:
        """Return the current proxy URL, or ``None`` to connect directly."""

    def report_block(self, url: str) -> str | None:
        """Report a block on ``url`` and return the next proxy URL to use.

        Returns ``None`` when the rotation budget (``Budgets.proxy_retries``) is
        exhausted so callers stop retrying.
        """
