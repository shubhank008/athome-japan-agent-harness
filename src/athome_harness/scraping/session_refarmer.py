"""Sync HTTP adapter with a Patchright browser-session fallback (T09).

Implements the planned recovery loop for a curl-cffi worker that hits an
AtHome challenge block:

    HttpDom -> Request -> Error(BlockDetected) -> PlaywrightCookie -> Handoff
    -> HttpDom(rebound) -> Request

The :class:`HttpDomAdapter` is intentionally synchronous (SPEC FR-5 forbids
automatic async refarming inside it), so this module owns the orchestration as a
separate async layer. It delegates the actual fresh-session farming to a
:class:`PlaywrightCookieFetcher`-compatible callable, persists whatever session
state and handoff files the farmer produces, then rebuilds the HTTP adapter
bound to the fresh handoff and retries the request.

This is the only consumer that ties the synchronous HTTP adapter to the browser
farmer, keeping each concrete adapter single-purpose.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from athome_harness.scraping.base import BlockDetected, redact_url
from athome_harness.scraping.cookie_handoff import CookieHandoff

logger = logging.getLogger(__name__)


class SessionFarmer(Protocol):
    """Async producer of a fresh browser handoff (e.g. ``farm()``)."""

    async def farm(self) -> CookieHandoff:
        """Farm one usable browser session and return its handoff."""
        ...


class SessionRefarmer:
    """Retry an HTTP fetch through a freshly farmed browser session on block.

    A first attempt runs without a handoff so the cheap curl-cffi path wins.
    If that raises :class:`BlockDetected` (a 403/429, an AtHome puzzle page, or
    any captcha marker), the refarmer farms a new browser session, rebinds the
    HTTP adapter to that handoff, and retries the same URL once. Handing the
    rebound handoff to curl-cffi replays the exact headers and cookies the
    browser session just established.
    """

    def __init__(
        self,
        *,
        build_adapter: Callable[[CookieHandoff | None], object],
        farm: Callable[[], Awaitable[CookieHandoff]],
        max_refarms: int = 1,
    ) -> None:
        """Configure the fallback loop around an adapter factory and farmer.

        ``build_adapter`` receives the handoff (or ``None`` for the direct
        attempt) and returns the synchronous scraper to call. ``farm`` yields a
        fresh :class:`CookieHandoff`. ``max_refarms`` bounds how many times a
        block may trigger a fresh browser session before giving up.
        """
        if max_refarms < 0:
            raise ValueError("max_refarms must not be negative")
        self._build_adapter = build_adapter
        self._farm = farm
        self._max_refarms = max_refarms

    async def fetch_html(self, url: str) -> str:
        """Fetch ``url``, refarming a browser session on block, and return HTML."""
        return await self._fetch(url, kind="html")  # type: ignore[return-value]

    async def fetch_binary(self, url: str) -> bytes:
        """Fetch ``url`` bytes with the same refarm-on-block recovery loop."""
        return await self._fetch(url, kind="binary")  # type: ignore[return-value]

    async def _fetch(self, url: str, *, kind: str) -> object:
        """Run a direct fetch, then refarm and retry when the site blocks."""
        active = self._build_adapter(None)
        try:
            try:
                return self._call(active, url, kind)
            except BlockDetected as first_block:
                logger.warning(
                    "[REHANDOFF_TRIGGERED] url=<%s> signature=<%s> refarms=<%d>",
                    redact_url(url),
                    first_block.signature,
                    self._max_refarms,
                )
                for _ in range(self._max_refarms):
                    handoff = await self._farm()
                    logger.warning(
                        "[REHANDOFF_FARMED] proxy=<%s> cookies=<%d>",
                        handoff.proxy_identity,
                        len(handoff.cookies),
                    )
                    rebound = self._build_adapter(handoff)
                    close_prev = getattr(active, "close", None)
                    if close_prev is not None:
                        close_prev()
                    active = rebound
                    try:
                        return self._call(rebound, url, kind)
                    except BlockDetected as block:
                        logger.warning(
                            "[REHANDOFF_STILL_BLOCKED] url=<%s> signature=<%s>",
                            redact_url(url),
                            block.signature,
                        )
                raise first_block
        finally:
            close = getattr(active, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _call(scraper: object, url: str, kind: str) -> object:
        """Invoke the matching synchronous fetch method on ``scraper``."""
        method = getattr(scraper, f"fetch_{kind}")
        return method(url)
