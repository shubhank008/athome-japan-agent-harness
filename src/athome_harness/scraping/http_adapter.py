"""HTTP DOM scraper adapter using curl-cffi and selectolax (T07).

Concrete :class:`BaseScraper` implementation that talks to AtHome through
curl-cffi with a browser impersonation profile. It presents browser-like headers,
retries transient failures with exponential backoff, detects IP blocks
(403/429/captcha markers) and HTTP 200 AtHome puzzle/authentication pages via
precise body markers and, when an optional :class:`ProxyProvider` is configured,
rotates to a proxy on block and recovers. A Patchright :class:`CookieHandoff` is
pinned to its original proxy and raises a block for the caller to refarm.

This is one of only two modules (the other is ``playwright_adapter.py``) allowed
to import a third-party HTTP/scraping library, per the Abstract First invariant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol, cast

from curl_cffi import requests as curl_requests
from selectolax.parser import HTMLParser

from athome_harness.config import Budgets
from athome_harness.scraping.base import (
    BaseScraper,
    BlockDetected,
    BlockSignature,
    ProxyProvider,
    redact_url,
)
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.cookie_handoff import (
    CookieHandoff,
    ImpersonateProfile,
    proxy_identity,
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


def _detect_athome_challenge(body: str) -> str | None:
    """Return the shared AtHome challenge classification for compatibility."""
    return detect_athome_challenge(body)


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


class CurlResponse(Protocol):
    """Minimal response surface required from curl-cffi or a test session."""

    @property
    def content(self) -> bytes:
        """Return the raw response body."""
        ...

    @property
    def status_code(self) -> int:
        """Return the HTTP status code."""
        ...

    @property
    def text(self) -> str:
        """Return the decoded response body."""
        ...


class CurlSession(Protocol):
    """Minimal curl-cffi session surface used by the concrete adapter."""

    def get(self, url: str, **kwargs: object) -> CurlResponse:
        """Perform one GET request."""
        ...

    def close(self) -> None:
        """Release the session resources."""
        ...


class HttpDomAdapter(BaseScraper):
    """Fetch AtHome pages over curl-cffi and detect blocks.

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

    When ``debug=True`` the adapter captures the last raw curl-cffi response
    object, accessible via the :attr:`raw_response` property. This is intended
    for operator probe scripts; production adapters leave it disabled.
    """

    def __init__(
        self,
        budgets: Budgets,
        *,
        proxy_provider: ProxyProvider | None = None,
        handoff: CookieHandoff | None = None,
        impersonate: ImpersonateProfile = "chrome",
        client: CurlSession | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        debug: bool = False,
    ) -> None:
        """Configure a curl-cffi adapter, optionally bound to a browser handoff.

        When *debug* is ``True`` the adapter captures each raw curl-cffi
        response in :attr:`raw_response` for operator inspection.
        """
        self._budgets = budgets
        self._proxy_provider = None if handoff is not None else proxy_provider
        self._handoff = handoff
        self._impersonate = handoff.impersonate if handoff is not None else impersonate
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        initial_proxy = handoff.proxy_url if handoff is not None else None
        self._client_owned = client is None
        self._client: CurlSession = client or self._build_client(initial_proxy)
        self._debug = debug or False
        self._raw_response: CurlResponse | None = None
        if handoff is not None:
            logger.warning(
                "[CURL_HANDOFF_BOUND] proxy=<%s> cookies=<%d> impersonate=<%s>",
                proxy_identity(handoff.proxy_url),
                len(handoff.cookies),
                self._impersonate,
            )

    def _build_client(self, proxy: str | None) -> CurlSession:
        """Build a curl-cffi session with the selected browser fingerprint."""
        return cast(
            CurlSession,
            curl_requests.Session(
                impersonate=self._impersonate,
                default_headers=False,
                proxy=proxy,
            ),
        )

    def _request_kwargs(self, proxy_url: str | None) -> dict[str, object]:
        """Build redaction-safe request options for one exact session identity."""
        if self._handoff is not None:
            options = self._handoff.to_curl_cffi_kwargs()
        else:
            options = {
                "headers": dict(BROWSER_HEADERS),
                "default_headers": False,
                "impersonate": self._impersonate,
            }
        options["timeout"] = self._budgets.http_timeout_s
        if proxy_url is not None:
            options["proxy"] = proxy_url
        else:
            options.pop("proxy", None)
        return options

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
        proxy_url: str | None = self._handoff.proxy_url if self._handoff else None
        attempts = (
            0
            if self._handoff is not None
            else (self._budgets.proxy_retries if self._proxy_provider else 0)
        )

        for attempt in range(attempts + 1):
            response = self._http_get(url, proxy_url)
            if self._debug:
                self._raw_response = response
            challenge_kind = _detect_athome_challenge(response.text)
            if challenge_kind is not None:
                logger.warning(
                    "[ATHOME_CHALLENGE] url=<%s> kind=<%s> htmlLength=<%s>",
                    redact_url(url),
                    challenge_kind,
                    len(response.text),
                )
            signature = _detect_signature(response.status_code, response.text)
            if signature is not None:
                if self._handoff is not None:
                    logger.warning(
                        "[CURL_BLOCK_REHANDOFF] url=<%s> signature=<%s>",
                        redact_url(url),
                        signature,
                    )
                    raise BlockDetected(url, signature)
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

    def _http_get(self, url: str, proxy_url: str | None) -> CurlResponse:
        """GET ``url``, retrying curl transport errors with exponential backoff."""
        if proxy_url is not None and self._handoff is None and self._client_owned:
            self._client.close()
            self._client = self._build_client(proxy_url)
        options = self._request_kwargs(proxy_url)
        logger.warning(
            "[CURL_REQUEST] url=<%s> impersonate=<%s> timeout=<%s>",
            redact_url(url),
            options.get("impersonate", "chrome"),
            self._budgets.http_timeout_s,
        )
        for attempt in range(_TRANSIENT_RETRIES):
            try:
                return self._client.get(url, **options)
            except curl_requests.errors.RequestsError:
                if attempt == _TRANSIENT_RETRIES - 1:
                    raise
                self._sleep(min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * (2**attempt)))
        raise AssertionError("unreachable")

    @property
    def raw_response(self) -> CurlResponse | None:
        """Last raw curl-cffi response captured when debug mode is enabled."""
        return self._raw_response

    def close(self) -> None:
        """Release the underlying curl-cffi transport."""
        self._client.close()
