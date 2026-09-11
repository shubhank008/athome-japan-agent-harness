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

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
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
    browser session just established. The bound adapter is retained for later
    fetches in the same lifecycle and must be closed by the owner.
    """

    def __init__(
        self,
        *,
        build_adapter: Callable[[CookieHandoff | None], object],
        farm: Callable[[str], Awaitable[CookieHandoff]],
        max_refarms: int = 1,
        debug: bool = False,
        debug_dir: Path = Path("debug"),
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
        self._active: object | None = None
        self._handoff: CookieHandoff | None = None
        self._debug = debug
        self._debug_dir = debug_dir

    async def fetch_html(self, url: str) -> str:
        """Fetch ``url``, refarming a browser session on block, and return HTML."""
        return await self._fetch(url, kind="html")  # type: ignore[return-value]

    async def fetch_binary(self, url: str) -> bytes:
        """Fetch ``url`` bytes with the same refarm-on-block recovery loop."""
        return await self._fetch(url, kind="binary")  # type: ignore[return-value]

    async def _fetch(self, url: str, *, kind: str) -> object:
        """Fetch through the cached adapter, farming only after a block."""
        if self._active is None:
            self._active = self._build_adapter(self._handoff)
        try:
            result = self._call(self._active, url, kind)
            self._capture_result(url, kind, result)
            return result
        except BlockDetected as first_block:
            logger.warning(
                "[REHANDOFF_TRIGGERED] url=<%s> signature=<%s> refarms=<%d>",
                redact_url(url),
                first_block.signature,
                self._max_refarms,
            )
            for _ in range(self._max_refarms):
                try:
                    handoff = await self._farm(url)
                except Exception as error:
                    self._capture_failure(url, error, "handoff")
                    raise
                logger.warning(
                    "[REHANDOFF_FARMED] proxy=<%s> cookies=<%d>",
                    handoff.proxy_identity,
                    len(handoff.cookies),
                )
                rebound = self._build_adapter(handoff)
                self._close_adapter(self._active)
                self._active = rebound
                self._handoff = handoff
                try:
                    result = self._call(rebound, url, kind)
                    self._capture_result(url, kind, result, post_handoff=True)
                    return result
                except BlockDetected as block:
                    logger.warning(
                        "[REHANDOFF_STILL_BLOCKED] url=<%s> signature=<%s>",
                        redact_url(url),
                        block.signature,
                    )
                    if self._debug:
                        self._capture_failure(url, block, "rebound")
                        adapter = self._active
                        raw = getattr(adapter, "raw_response", None)
                        if raw is not None and isinstance(getattr(raw, "text", None), str):
                            (self._debug_dir / "live_last_handoff_challenge.html").write_text(
                                raw.text, encoding="utf-8"
                            )
            raise first_block

    def close(self) -> None:
        """Close the cached adapter and discard its handoff for this lifecycle."""
        self._close_adapter(self._active)
        self._active = None
        self._handoff = None

    def _capture_result(
        self, url: str, kind: str, result: object, *, post_handoff: bool = False
    ) -> None:
        """Persist safe HTML diagnostics and per-request metadata when DEBUG is enabled."""
        if not self._debug or kind != "html" or not isinstance(result, str):
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        from athome_harness.scraping.challenge import detect_athome_challenge

        challenge = detect_athome_challenge(result)
        if challenge is None or post_handoff:
            filename = "live_last_handoff.html" if post_handoff else "live_last_success.html"
            (self._debug_dir / filename).write_text(result, encoding="utf-8")
        if challenge is not None and post_handoff:
            (self._debug_dir / "live_last_handoff_challenge.html").write_text(
                result, encoding="utf-8"
            )

    def _capture_failure(self, url: str, error: BaseException, stage: str) -> None:
        """Persist stable redacted failure metadata when DEBUG is enabled."""
        if not self._debug:
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        safe_url = redact_url(url)
        (self._debug_dir / f"live_{stage}_failure.json").write_text(
            json.dumps(
                {"url": safe_url, "error": type(error).__name__, "message": str(error)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _close_adapter(adapter: object | None) -> None:
        """Close an adapter when it exposes the scraper close contract."""
        if adapter is not None:
            close = getattr(adapter, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _call(scraper: object, url: str, kind: str) -> object:
        """Invoke the matching synchronous fetch method on ``scraper``."""
        method = getattr(scraper, f"fetch_{kind}")
        return method(url)
