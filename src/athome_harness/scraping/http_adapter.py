"""HTTP DOM scraper adapter using httpx and selectolax (T07).

Concrete :class:`BaseScraper` implementation that talks to AtHome over plain
HTTP. It presents browser-like headers, retries transient failures with
exponential backoff, detects IP blocks (403/429/captcha markers) and HTTP 200
AtHome puzzle/authentication pages via precise body markers and, when an
optional :class:`ProxyProvider` is configured, rotates to a proxy on block and
recovers. Proxy rotation and challenge events emit the contract markers
verbatim with redacted URLs.

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
    redact_url,
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

# Substrings that mark a response body as an AtHome anti-bot challenge page,
# even when the HTTP status is 200 (US-008, T09a). AtHome can answer with a
# puzzle/authentication page instead of content; these precise markers are
# detected before parsing or saving so a challenge page never becomes listing
# data. A challenge is never solved or circumvented: the adapter only rotates
# through the configured proxy path and degrades to BlockDetected when bounded
# alternate requests are exhausted.
_ATHOME_PUZZLE_MARKERS = (
    "click to verify",
    "認証にご協力ください",
)
_ATHOME_JAVASCRIPT_MARKERS = (
    "to regain access, please make sure that cookies and javascript are enabled",
)

# Transient-failure retry policy. Not a config knob: the SPEC budgets table has
# no HTTP backoff entry, so these stay as documented constants.
_TRANSIENT_RETRIES = 3
_BACKOFF_BASE_S = 0.5
_BACKOFF_MAX_S = 8.0


def _detect_athome_challenge(body: str) -> str | None:
    """Return the kind of AtHome anti-bot challenge in ``body``, or ``None``.

    Case-insensitive exact marker match against the AtHome puzzle page
    (``[ATHOME_CHALLENGE] kind=puzzle``) and the Chrome JavaScript/cookie
    interstitial (``kind=javascript``). These challenge pages can arrive with
    HTTP 200, so the status code is not consulted here; the caller decides
    whether to emit the marker and how to classify the block.
    """
    lowered = body.lower()
    if any(marker in lowered for marker in _ATHOME_PUZZLE_MARKERS):
        return "puzzle"
    if any(marker in lowered for marker in _ATHOME_JAVASCRIPT_MARKERS):
        return "javascript"
    return None


def _detect_signature(status_code: int, body: str) -> BlockSignature | None:
    """Map an HTTP response to a block signature, or ``None`` when not blocked.

    HTTP 403 and 429 are hard block signals; any body containing a generic
    captcha marker or an AtHome challenge marker is classified as ``captcha``
    regardless of the status code. Note 429 is therefore both a block signal and
    a retryable code; the adapter treats it as a block signal whenever it
    appears, per SPEC section 4.
    """
    lowered = body.lower()
    if _detect_athome_challenge(body) is not None or any(
        marker in lowered for marker in _CAPTCHA_MARKERS
    ):
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

    AtHome can also answer with an HTTP 200 puzzle/authentication page. Such a
    page is detected via precise body markers, emits the ``[ATHOME_CHALLENGE]``
    contract marker with a redacted URL, and is classified as a captcha block so
    it follows the same bounded proxy path and never reaches parsers or fixture
    files. The challenge itself is never solved or circumvented.

    A plain successful GET is subject to exponential-backoff retries for
    connection failures only; those are silent at the marker level because the
    marker contract only names block and rotation events.
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

    def _build_client(self, proxy: str | None) -> httpx.Client:
        """Build an httpx.Client with browser headers and the budget timeout."""
        return httpx.Client(
            headers=BROWSER_HEADERS,
            timeout=httpx.Timeout(self._budgets.http_timeout_s),
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
            challenge_kind = _detect_athome_challenge(response.text)
            if challenge_kind is not None:
                logger.warning(
                    "[ATHOME_CHALLENGE] url=<%s> kind=<%s>",
                    redact_url(url),
                    challenge_kind,
                )
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
