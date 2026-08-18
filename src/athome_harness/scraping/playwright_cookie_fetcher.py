"""Async Playwright farmer for AtHome browser-session cookies."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, cast

from playwright.async_api import Browser, Page, ProxySettings, Request, async_playwright

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.cookie_handoff import CookieHandoff, proxy_identity

try:
    from playwright_stealth import (
        stealth_async as _legacy_stealth_async,
    )
except ImportError:
    from playwright_stealth import Stealth  # type: ignore[import-untyped]

    async def _legacy_stealth_async(page: Page) -> None:
        """Apply the current playwright-stealth API under the legacy name."""
        await Stealth().apply_stealth_async(page)


logger = logging.getLogger(__name__)

DEFAULT_BROAD_SEARCH_URL: Final = "https://www.athome.co.jp/chintai/osaka/list/"
DEFAULT_DEBUG_DIR: Final = Path("debug")
DEFAULT_WAIT_SECONDS: Final = 3.0
MIN_RENDERED_HTML_LENGTH: Final = 200
_CLICK_TEXT = re.compile(r"click\s+to\s+verify", re.IGNORECASE)


class PlaywrightCookieFetcherError(RuntimeError):
    """Raised when Playwright cannot produce a usable browser handoff."""


class PlaywrightCookieFetcher:
    """Farm a short-lived AtHome browser session for curl-cffi workers."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_BROAD_SEARCH_URL,
        proxy_url: str | None = None,
        debug_dir: Path = DEFAULT_DEBUG_DIR,
        handoff_path: Path | None = None,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        min_html_length: int = MIN_RENDERED_HTML_LENGTH,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Configure one browser farm without starting a browser yet."""
        if wait_seconds < 0:
            raise ValueError("wait_seconds must not be negative")
        if min_html_length < 1:
            raise ValueError("min_html_length must be positive")
        self._url = url
        self._proxy_url = proxy_url
        self._debug_dir = debug_dir
        self._handoff_path = handoff_path or (
            debug_dir / f"cookie_handoff_{proxy_identity(proxy_url)}.json"
        )
        self._wait_seconds = wait_seconds
        self._min_html_length = min_html_length
        self._sleep = sleep_fn or asyncio.sleep

    async def farm(self) -> CookieHandoff:
        """Render AtHome, optionally verify once, and persist the handoff."""
        logger.warning(
            "[PLAYWRIGHT_FARM_START] url=<%s> proxy=<%s>",
            redact_url(self._url),
            proxy_identity(self._proxy_url),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                proxy=self._playwright_proxy(),
            )
            try:
                return await self._farm_in_browser(browser)
            finally:
                await browser.close()

    async def _farm_in_browser(self, browser: Browser) -> CookieHandoff:
        """Run the page workflow inside a browser that the caller owns."""
        context = await browser.new_context(locale="ja-JP")
        try:
            page = await context.new_page()
            await _legacy_stealth_async(page)
            request_headers: dict[str, str] = {}

            def remember_request_headers(request: Request) -> None:
                """Remember headers from the main navigation request only."""
                if request.is_navigation_request():
                    request_headers.update(dict(request.headers))

            page.on("request", remember_request_headers)
            await page.goto(self._url, wait_until="domcontentloaded")
            await self._sleep(self._wait_seconds)
            html = await page.content()
            user_agent = await page.evaluate("navigator.userAgent")
            await self._validate_render(html, request_headers, blocked_allowed=True)

            challenge_kind = detect_athome_challenge(html)
            if challenge_kind is not None:
                logger.warning("[PLAYWRIGHT_CHALLENGE] kind=<%s>", challenge_kind)
                await self._save_debug_capture(page, "before", html)
                clicked = await self._click_verification(page)
                logger.warning("[PLAYWRIGHT_VERIFY] clicked=<%s>", str(clicked).lower())
                await self._sleep(self._wait_seconds)
                html = await page.content()
                await self._save_debug_capture(page, "after", html)
                await self._validate_render(html, request_headers, blocked_allowed=False)

            cookies = await context.cookies()
            if not cookies:
                self._reject("render")
            if not request_headers:
                request_headers = {"User-Agent": user_agent}
            handoff = CookieHandoff.from_browser(
                proxy_url=self._proxy_url,
                user_agent=user_agent,
                headers=request_headers,
                cookies=[dict(cookie) for cookie in cookies],
            )
            handoff.save(self._handoff_path, self._debug_dir / "cookies.txt")
            logger.warning(
                "[PLAYWRIGHT_HANDOFF_SAVED] proxy=<%s> cookies=<%d>",
                handoff.proxy_identity,
                len(handoff.cookies),
            )
            return handoff
        finally:
            await context.close()

    async def _validate_render(
        self,
        html: str,
        headers: dict[str, str],
        *,
        blocked_allowed: bool,
    ) -> None:
        """Reject short or challenged HTML and emit a render marker."""
        challenge_kind = detect_athome_challenge(html)
        blocked = challenge_kind is not None
        logger.warning(
            "[PLAYWRIGHT_RENDERED] html_chars=<%d> blocked=<%s>",
            len(html),
            str(blocked).lower(),
        )
        if not headers:
            self._reject("render")
        if blocked and not blocked_allowed:
            self._reject("challenge")
        if len(html.strip()) < self._min_html_length and not (blocked and blocked_allowed):
            self._reject("render")

    async def _click_verification(self, page: Page) -> bool:
        """Click a visible verification control, never a puzzle-piece target."""
        locator = page.get_by_text(_CLICK_TEXT).first
        if await locator.count() == 0 or not await locator.is_visible():
            return False
        await locator.click()
        return True

    async def _save_debug_capture(self, page: Page, stage: str, html: str) -> None:
        """Save raw HTML and a screenshot for one challenge stage."""
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        (self._debug_dir / f"playwright_{stage}.html").write_text(
            html,
            encoding="utf-8",
        )
        await page.screenshot(path=str(self._debug_dir / f"playwright_{stage}.png"))

    def _playwright_proxy(self) -> ProxySettings | None:
        """Translate the configured proxy URL to Playwright launch settings."""
        if self._proxy_url is None:
            return None
        return cast(ProxySettings, {"server": self._proxy_url})

    def _reject(self, reason: str) -> None:
        """Raise a stable rejection marker without returning challenged content."""
        logger.warning("[PLAYWRIGHT_HANDOFF_REJECTED] reason=<%s>", reason)
        raise PlaywrightCookieFetcherError(f"[PLAYWRIGHT_HANDOFF_REJECTED] reason=<{reason}>")
