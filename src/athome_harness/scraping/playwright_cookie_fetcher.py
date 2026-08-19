"""Async Patchright farmer for AtHome browser-session cookies."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

import aiohttp
from patchright.async_api import (
    BrowserContext,
    Frame,
    Locator,
    Page,
    ProxySettings,
    Request,
    async_playwright,
)

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.cookie_handoff import CookieHandoff, proxy_identity
from athome_harness.scraping.session_state import (
    SessionState,
    get_installed_chrome_version,
)

logger = logging.getLogger(__name__)

DEFAULT_BROAD_SEARCH_URL: Final = "https://www.athome.co.jp/chintai/osaka/list/"
DEFAULT_DEBUG_DIR: Final = Path("debug")
DEFAULT_WAIT_SECONDS: Final = 3.0
DEFAULT_CLICK_HOLD_SECONDS: Final = 2.5
MIN_RENDERED_HTML_LENGTH: Final = 200
_CLICK_TEXT = re.compile(r"click(?:\s+here)?\s+to\s+verify", re.IGNORECASE)


async def _solve_turnstile_capsolver(api_key: str, site_url: str, site_key: str) -> str | None:
    """Solve Cloudflare Turnstile using CapSolver API."""
    logger.warning("[CAPSOLVER_TURNSTILE] url=<%s> site_key=<%s>", site_url, site_key)
    endpoint = "https://api.capsolver.com/createTask"

    payload = {
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": site_url,
            "websiteKey": site_key,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()
            if data.get("errorId", 0) != 0:
                logger.warning("[CAPSOLVER_TURNSTILE] error=<%s>", data.get("errorDescription"))
                return None
            task_id = data.get("taskId")

        result_url = "https://api.capsolver.com/getTaskResult"
        for _ in range(30):
            await asyncio.sleep(2)
            async with session.post(
                result_url, json={"clientKey": api_key, "taskId": task_id}
            ) as res_resp:
                res_data = await res_resp.json()
                if res_data.get("status") == "ready":
                    logger.warning("[CAPSOLVER_TURNSTILE] solved")
                    return str(res_data["solution"]["token"])
                if res_data.get("status") == "failed":
                    logger.warning("[CAPSOLVER_TURNSTILE] failed")
                    return None
    return None


async def _solve_geetest_capsolver(
    api_key: str, site_url: str, gt: str, challenge: str
) -> dict[str, Any] | None:
    """Solve Geetest V3 using CapSolver API."""
    logger.warning("[CAPSOLVER_GEETEST] url=<%s> gt=<%s>", site_url, gt[:5])
    endpoint = "https://api.capsolver.com/createTask"

    payload = {
        "clientKey": api_key,
        "task": {
            "type": "GeeTestTaskProxyLess",
            "websiteURL": site_url,
            "gt": gt,
            "challenge": challenge,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()
            if data.get("errorId", 0) != 0:
                logger.warning("[CAPSOLVER_GEETEST] error=<%s>", data.get("errorDescription"))
                return None
            task_id = data.get("taskId")

        result_url = "https://api.capsolver.com/getTaskResult"
        for _ in range(30):
            await asyncio.sleep(2)
            async with session.post(
                result_url, json={"clientKey": api_key, "taskId": task_id}
            ) as res_resp:
                res_data = await res_resp.json()
                if res_data.get("status") == "ready":
                    logger.warning("[CAPSOLVER_GEETEST] solved")
                    return dict(res_data["solution"])
                if res_data.get("status") == "failed":
                    logger.warning("[CAPSOLVER_GEETEST] failed")
                    return None
    return None


class PlaywrightCookieFetcherError(RuntimeError):
    """Raised when Patchright cannot produce a usable browser handoff."""


class PlaywrightCookieFetcher:
    """Farm a short-lived AtHome browser session for curl-cffi workers.

    Single-purpose and lean: it renders AtHome, performs one bounded
    verification click if challenged, and persists the handoff plus
    ``session_state.json``. It never captures screenshots, trace, video, or an
    event log; that diagnostic overhead lives in the operator probe.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_BROAD_SEARCH_URL,
        proxy_url: str | None = None,
        debug_dir: Path = DEFAULT_DEBUG_DIR,
        handoff_path: Path | None = None,
        session_state_path: Path | None = None,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        min_html_length: int = MIN_RENDERED_HTML_LENGTH,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        click_hold_seconds: float = DEFAULT_CLICK_HOLD_SECONDS,
        capsolver_key: str | None = None,
    ) -> None:
        """Configure one browser farm without starting a browser yet.

        The production farmer stays lean: it persists only the session handoff
        and ``session_state.json``. Screenshots, browser trace, video, and the
        structured event log are owned by the operator probe
        (``scripts/playwright_manual_probe.py`` DEBUG mode), not this adapter.
        """
        if wait_seconds < 0:
            raise ValueError("wait_seconds must not be negative")
        if min_html_length < 1:
            raise ValueError("min_html_length must be positive")
        if click_hold_seconds < 0:
            raise ValueError("click_hold_seconds must not be negative")
        self._url = url
        self._proxy_url = proxy_url
        self._debug_dir = debug_dir
        self._handoff_path = handoff_path or (
            debug_dir / f"cookie_handoff_{proxy_identity(proxy_url)}.json"
        )
        self._session_state_path = session_state_path or (debug_dir / "session_state.json")
        self._wait_seconds = wait_seconds
        self._min_html_length = min_html_length
        self._sleep = sleep_fn or asyncio.sleep
        self._click_hold_seconds = click_hold_seconds
        self._capsolver_key = capsolver_key

    async def farm(self) -> CookieHandoff:
        """Render AtHome, optionally verify once, and persist the handoff."""
        logger.warning(
            "[PATCHRIGHT_FARM_START] url=<%s> proxy=<%s>",
            redact_url(self._url),
            proxy_identity(self._proxy_url),
        )
        async with async_playwright() as patchright:
            with tempfile.TemporaryDirectory(prefix="athome-patchright-") as user_data_dir:
                context = await patchright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel="chrome",
                    headless=True,
                    no_viewport=True,
                    proxy=self._playwright_proxy(),
                    **cast(Any, self._context_options()),
                )
                return await self._farm_in_context(context)

    def _context_options(self) -> dict[str, object]:
        """Return context options shared by persistent Chrome sessions."""
        return {"locale": "ja-JP"}

    async def _farm_in_context(self, context: BrowserContext) -> CookieHandoff:
        """Run the page workflow inside a persistent browser context."""
        try:
            page = await context.new_page()
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
                solved = await self._try_capsolver_solve(page, html, challenge_kind)
                if not solved:
                    clicked = await self._click_verification(page)
                    logger.warning("[PLAYWRIGHT_VERIFY] clicked=<%s>", str(clicked).lower())
                await self._sleep(self._wait_seconds)
                html = await page.content()
                after_kind = detect_athome_challenge(html)
                accepted = after_kind is None
                logger.warning(
                    "[PLAYWRIGHT_VERIFY_RESULT] solved=%s accepted=<%s> "
                    "challenge_kind=<%s> html_chars=<%d>",
                    str(solved).lower(),
                    str(accepted).lower(),
                    after_kind or "none",
                    len(html),
                )
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
            self._persist_session_state(
                cookies=[dict(cookie) for cookie in cookies],
                user_agent=user_agent,
                headers=request_headers,
            )
            logger.warning(
                "[PLAYWRIGHT_HANDOFF_SAVED] proxy=<%s> cookies=<%d>",
                handoff.proxy_identity,
                len(handoff.cookies),
            )
            return handoff
        finally:
            try:
                await context.close()
            except Exception:
                logger.exception("[PATCHRIGHT_CONTEXT_CLOSE_FAILED]")

    def _persist_session_state(
        self,
        *,
        cookies: list[dict[str, Any]],
        user_agent: str,
        headers: dict[str, str],
    ) -> None:
        """Persist the curl-cffi ``session_state.json`` beside the handoff.

        The real navigation ``headers`` and ``user_agent`` from the browser are
        preferred over the synthetic Chrome envelope so curl-cffi replays the
        exact fingerprint AtHome just accepted.
        """
        state = SessionState.from_browser(
            cookies=cookies,
            user_agent=user_agent,
            chrome_version=get_installed_chrome_version(),
            proxy_url=self._proxy_url,
        )
        state.headers = dict(headers)
        if "user-agent" in state.headers and not state.headers.get("user-agent"):
            state.headers["user-agent"] = user_agent
        state.user_agent = user_agent
        state.save(self._session_state_path)
        logger.warning(
            "[PLAYWRIGHT_SESSION_STATE_SAVED] path=<%s> cookies=<%d>",
            self._session_state_path,
            len(cookies),
        )

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
        """Perform one observable click attempt without solving a puzzle."""
        target, frame, target_kind = await self._find_verification_target(page)
        if target is None or frame is None:
            logger.warning("[PLAYWRIGHT_VERIFY_TARGET] found=false")
            return False
        try:
            await target.wait_for(state="visible", timeout=5000)
            box = await target.bounding_box()
            logger.warning(
                "[PLAYWRIGHT_VERIFY_TARGET] found=true frame=<%d> kind=<%s> visible=true box=<%s>",
                self._frame_index(page, frame),
                target_kind,
                box,
            )
            await target.hover()
            await self._sleep(0.5)
            started = datetime.now(UTC)
            logger.warning("[PLAYWRIGHT_VERIFY_POINTER] action=<click_start>")
            mouse = getattr(page, "mouse", None)
            if box is not None and mouse is not None:
                await mouse.move(box["x"] + box["width"] / 3, box["y"] + box["height"] / 3)
                await mouse.down()
                await self._sleep(self._click_hold_seconds)
                await mouse.up()
            else:
                await target.click(delay=250)
            ended = datetime.now(UTC)
            duration_ms = (ended - started).total_seconds() * 1000
            logger.warning(
                "[PLAYWRIGHT_VERIFY_POINTER] action=<click_end> duration_ms=<%.0f>",
                duration_ms,
            )
            return True
        except Exception as error:
            logger.warning("[PLAYWRIGHT_VERIFY] interaction_error=<%s>", type(error).__name__)
            return False

    async def _try_capsolver_solve(
        self, page: Page, html: str, challenge_kind: str
    ) -> bool:
        """Attempt to solve a WAF challenge via CapSolver. Returns True on success."""
        if not self._capsolver_key:
            return False

        if challenge_kind == "puzzle":
            gt_match = re.search(r'gt:\s*["\']([^"\']+)["\']', html)
            challenge_match = re.search(r'challenge:\s*["\']([^"\']+)["\']', html)
            data_match = re.search(r'data:\s*["\'](3:[^"\']+)["\']', html)

            if gt_match and challenge_match and data_match:
                gt = gt_match.group(1)
                challenge = challenge_match.group(1)
                incapsula_data = data_match.group(1)

                logger.warning("[CAPSOLVER_GEETEST] extracting params from puzzle")
                solution = await _solve_geetest_capsolver(
                    self._capsolver_key, page.url, gt, challenge
                )

                if solution:
                    payload = {
                        "geetest_challenge": solution.get("challenge"),
                        "geetest_validate": solution.get("validate"),
                        "geetest_seccode": solution.get("seccode"),
                        "data": incapsula_data,
                    }
                    logger.warning("[CAPSOLVER_GEETEST] injecting solution")
                    await page.evaluate(f"solvedCaptcha({json.dumps(payload)})")
                    await self._sleep(5.0)
                    return True
            else:
                logger.warning("[CAPSOLVER_GEETEST] failed to extract params from HTML")
        else:
            site_key = await page.evaluate(
                "() => document.querySelector('[data-sitekey]')"
                "?.getAttribute('data-sitekey') || ''"
            )
            if site_key:
                token = await _solve_turnstile_capsolver(
                    self._capsolver_key, page.url, site_key
                )
                if token:
                    await page.evaluate(
                        """
                        (token) => {
                            const input =
                                document.querySelector('input[name="cf-turnstile-response"]')
                                || document.createElement('input');
                            input.value = token;
                            document.forms[0]?.submit();
                        }
                        """,
                        token,
                    )
                    await self._sleep(5.0)
                    return True
        return False

    async def _find_verification_target(
        self,
        page: Page,
    ) -> tuple[Locator | None, Frame | None, str | None]:
        """Find a visible semantic control in the main page or child frames."""
        for frame in page.frames:
            get_by_role = getattr(frame, "get_by_role", None)
            if get_by_role is not None:
                for role in ("button", "link"):
                    locator = get_by_role(role, name=_CLICK_TEXT).first
                    if await locator.count() > 0:
                        return locator, frame, role
            locator = frame.get_by_text(_CLICK_TEXT).first
            if await locator.count() > 0:
                return locator, frame, "text"
        return None, None, None

    @staticmethod
    def _frame_index(page: Page, frame: Frame) -> int:
        """Return a stable frame index for diagnostic logs."""
        return next(
            (index for index, candidate in enumerate(page.frames) if candidate == frame),
            -1,
        )

    def _playwright_proxy(self) -> ProxySettings | None:
        """Translate the configured proxy URL to Patchright launch settings."""
        if self._proxy_url is None:
            return None
        return cast(ProxySettings, {"server": self._proxy_url})

    def _reject(self, reason: str) -> None:
        """Raise a stable rejection marker without returning challenged content."""
        logger.warning("[PLAYWRIGHT_HANDOFF_REJECTED] reason=<%s>", reason)
        raise PlaywrightCookieFetcherError(f"[PLAYWRIGHT_HANDOFF_REJECTED] reason=<{reason}>")
