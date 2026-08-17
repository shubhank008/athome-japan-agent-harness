"""HTTP DOM scraper adapter using httpx and selectolax (T07).

Concrete :class:`BaseScraper` implementation that talks to AtHome over plain
HTTP. It presents browser-like headers, retries transient failures with
exponential backoff, detects IP blocks (403/429/captcha markers) and, when an
optional :class:`ProxyProvider` is configured, rotates to a proxy on block and
recovers. Proxy rotation emits the contract markers verbatim.

This is one of only two modules (the other is ``playwright_adapter.py``) allowed
to import a third-party HTTP/scraping library, per the Abstract First invariant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx
from selectolax.parser import HTMLParser

from athome_harness.config import Budgets
from athome_harness.scraping.base import (
    BaseScraper,
    BlockDetected,
    BlockSignature,
    ProxyProvider,
)

logger = logging.getLogger(__name__)

# Browser-like header envelope presented on every request so AtHome sees a
# plausible desktop client expecting Japanese-language content.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, "
        "like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
}

# Substrings that mark a response body as a captcha challenge page.
_CAPTCHA_MARKERS = ("recaptcha", "captcha", "verify you are human")

# Transient-failure retry policy. Not a config knob: the SPEC budgets table has
# no HTTP backoff entry, so these stay as documented constants.
_TRANSIENT_RETRIES = 3
_BACKOFF_BASE_S = 0.5
_BACKOFF_MAX_S = 8.0

# Retryable transport/HTTP status codes. 403/429 are excluded: they are raised
# as :class:`BlockDetected` by the caller and must never be swallowed by retry.
_RETRYABLE_CODES = {408, 425, 429, 500, 502, 503, 504}


def _detect_signature(status_code: int, body: str) -> BlockSignature | None:
    """Map an HTTP response to a block signature, or ``None`` when not blocked.

    HTTP 403 and 429 are hard block signals; any body containing a captcha
    marker is classified as ``captcha`` regardless of the status code. Note 429
    is therefore both a block signal and a retryable code; the adapter treats it
    as a block signal whenever it appears, per SPEC section 4.
    """
    lowered = body.lower()
    if any(marker in lowered for marker in _CAPTCHA_MARKERS):
        return "captcha"
    if status_code in (403, 429):
        return "403" if status_code == 403 else "429"
    return None


class HttpDomAdapter(BaseScraper):
    """Fetch AtHome pages over httpx and detect blocks.

    A request that is not blocked returns the body unchanged (``fetch_html`` as
    text, ``fetch_binary`` as bytes, ``fetch_dom`` as a selectolax tree). When a
    block signal is detected:

    1. Without a proxy provider the adapter raises :class:`BlockDetected` at
       once with the detected signature.
    2. With a proxy provider the adapter reports the block, rotates through
       proxies (emitting [PROXY_ROTATE] and [PROXY_RECOVERED] markers) and
       retries the request, up to ``Budgets.proxy_retries`` proxy attempts.
       Direct connection always comes first; a proxy engages only on a block.

    A plain successful GET is subject to exponential-backoff retries only for
    transient errors (5xx and connection failures); those are silent at the
    marker level because the marker contract only names block and rotation
    events.
    """

    def __init__(
        self,
        budgets: Budgets,
        *,
        proxy_provider: ProxyProvider | None = None,
        client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._budgets = budgets
        self._proxy_provider = proxy_provider
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        self._client = client or self._build_client(None)

    @staticmethod
    def _build_client(proxy: str | None) -> httpx.Client:
        """Build an httpx.Client with browser headers and the budget timeout."""
        return httpx.Client(
            headers=BROWSER_HEADERS,
            timeout=httpx.Timeout(Budgets().http_timeout_s),
            proxy=proxy,
            follow_redirects=True,
        )

    def fetch_html(self, url: str) -> str:
        """Fetch ``url`` and return the decoded HTML body as text."""
        return self._request_bytes(url).decode("utf-8", errors="replace")

    def fetch_binary(self, url: str) -> bytes:
        """Fetch ``url`` and return the raw response body bytes."""
        return self._request_bytes(url)

    def fetch_dom(self, url: str) -> HTMLParser:
        """Fetch ``url`` and parse the HTML into a selectolax DOM tree."""
        return HTMLParser(self.fetch_html(url))

    def _request_bytes(self, url: str) -> bytes:
        """Run the direct-first fetch with proxy rotation and return body bytes.

        Direct connection is always tried first. On a block signal the adapter
        reports the block to the provider (which may return a proxy URL) and
        retries through it. Only when the provider is exhausted does the
        adapter raise :class:`BlockDetected`.
        """
        proxy_url: str | None = None
        attempts = self._budgets.proxy_retries if self._proxy_provider else 0

        for attempt in range(attempts + 1):
            response = self._http_get(url, proxy_url)
            signature = _detect_signature(response.status_code, response.text)
            if signature is not None:
                if self._proxy_provider is None:
                    raise BlockDetected(url, signature)
                proxy_url = self._proxy_provider.report_block(url)
                if proxy_url is None:
                    raise BlockDetected(url, signature)
                logger.warning(
                    "[PROXY_ROTATE] attempt=<%d> of=<%d>",
                    attempt + 1,
                    self._budgets.proxy_retries,
                )
                continue
            if proxy_url is not None:
                logger.warning("[PROXY_RECOVERED] via=proxy")
            return response.content
        raise BlockDetected(url, "429")

    def _http_get(self, url: str, proxy_url: str | None) -> httpx.Response:
        """GET ``url``, retrying transient errors with exponential backoff.

        When ``proxy_url`` is not ``None`` the client is rebuilt to use it; the
        direct connection (``proxy_url is None``) is always attempted first.
        """
        if proxy_url is not None:
            self._client.close()
            self._client = self._build_client(proxy_url)
        for attempt in range(_TRANSIENT_RETRIES):
            try:
                return self._client.get(url)
            except httpx.TransportError:
                if attempt == _TRANSIENT_RETRIES - 1:
                    raise
                self._sleep(min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * (2**attempt)))
        raise AssertionError("unreachable")

    def close(self) -> None:
        """Release the underlying httpx transport."""
        self._client.close()
