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

from patchright.async_api import (
    BrowserContext,
    Frame,
    Locator,
    Page,
    ProxySettings,
    Request,
    Video,
    async_playwright,
)

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
DEFAULT_CLICK_HOLD_SECONDS: Final = 2.5
MIN_RENDERED_HTML_LENGTH: Final = 200
_CLICK_TEXT = re.compile(r"click(?:\s+here)?\s+to\s+verify", re.IGNORECASE)
_DIAGNOSTIC_VIDEO_NAME: Final = "playwright_challenge.webm"
_DIAGNOSTIC_TRACE_NAME: Final = "playwright_challenge_trace.zip"
_DIAGNOSTIC_EVENTS_NAME: Final = "playwright_events.jsonl"


class PlaywrightCookieFetcherError(RuntimeError):
    """Raised when Patchright cannot produce a usable browser handoff."""


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
        diagnostics: bool = True,
        click_hold_seconds: float = DEFAULT_CLICK_HOLD_SECONDS,
    ) -> None:
        """Configure one browser farm without starting a browser yet."""
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
        self._wait_seconds = wait_seconds
        self._min_html_length = min_html_length
        self._sleep = sleep_fn or asyncio.sleep
        self._diagnostics = diagnostics
        self._click_hold_seconds = click_hold_seconds
        self._events_path = debug_dir / _DIAGNOSTIC_EVENTS_NAME
        self._trace_path = debug_dir / _DIAGNOSTIC_TRACE_NAME
        self._video_path = debug_dir / _DIAGNOSTIC_VIDEO_NAME

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
                self._event(
                    "context_started",
                    marker="[PATCHRIGHT_CONTEXT_STARTED]",
                    proxy=proxy_identity(self._proxy_url),
                )
                return await self._farm_in_context(context)

    def _context_options(self) -> dict[str, object]:
        """Return context options shared by persistent Chrome sessions."""
        options: dict[str, object] = {"locale": "ja-JP"}
        if self._diagnostics:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            options["record_video_dir"] = str(self._debug_dir)
            options["record_video_size"] = {"width": 1280, "height": 720}
        return options

    async def _farm_in_context(self, context: BrowserContext) -> CookieHandoff:
        """Run the page workflow inside a persistent browser context."""
        trace_started = False
        page_video: Video | None = None
        try:
            if self._diagnostics:
                await context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=False,
                )
                trace_started = True
                self._event("diagnostics_start", artifacts=self._artifact_names())
            page = await context.new_page()
            page_video = page.video
            request_headers: dict[str, str] = {}

            def remember_request_headers(request: Request) -> None:
                """Remember headers from the main navigation request only."""
                if request.is_navigation_request():
                    request_headers.update(dict(request.headers))

            page.on("request", remember_request_headers)
            await _legacy_stealth_async(page)
            await page.goto(self._url, wait_until="domcontentloaded")
            await self._sleep(self._wait_seconds)
            html = await page.content()
            user_agent = await page.evaluate("navigator.userAgent")
            await self._validate_render(html, request_headers, blocked_allowed=True)

            challenge_kind = detect_athome_challenge(html)
            if challenge_kind is not None:
                logger.warning("[PLAYWRIGHT_CHALLENGE] kind=<%s>", challenge_kind)
                self._event(
                    "challenge_before",
                    challenge_kind=challenge_kind,
                    html_chars=len(html),
                    url=redact_url(page.url),
                )
                await self._save_debug_capture(page, "before", html)
                clicked = await self._click_verification(page)
                logger.warning("[PLAYWRIGHT_VERIFY] clicked=<%s>", str(clicked).lower())
                await self._sleep(self._wait_seconds)
                html = await page.content()
                after_kind = detect_athome_challenge(html)
                accepted = after_kind is None
                logger.warning(
                    "[PLAYWRIGHT_VERIFY_RESULT] attempted=<%s> accepted=<%s> "
                    "challenge_kind=<%s> html_chars=<%d>",
                    str(clicked).lower(),
                    str(accepted).lower(),
                    after_kind or "none",
                    len(html),
                )
                self._event(
                    "verification_result",
                    attempted=clicked,
                    accepted=accepted,
                    challenge_kind=after_kind,
                    html_chars=len(html),
                    url=redact_url(page.url),
                )
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
            if trace_started:
                try:
                    await context.tracing.stop(path=str(self._trace_path))
                except Exception:
                    logger.exception("[PATCHRIGHT_TRACE_STOP_FAILED]")
            try:
                await context.close()
            except Exception:
                logger.exception("[PATCHRIGHT_CONTEXT_CLOSE_FAILED]")
            if page_video is not None:
                try:
                    await self._finalize_video(page_video)
                except Exception:
                    logger.exception("[PATCHRIGHT_VIDEO_FINALIZE_FAILED]")
            if self._diagnostics:
                logger.warning(
                    "[PLAYWRIGHT_DIAGNOSTICS_SAVED] events=<%s> trace=<%s> video=<%s>",
                    self._events_path,
                    self._trace_path,
                    self._video_path,
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
            self._event("verification_target", found=False)
            return False
        try:
            await target.wait_for(state="visible", timeout=5000)
            box = await target.bounding_box()
            frame_url = redact_url(frame.url)
            logger.warning(
                "[PLAYWRIGHT_VERIFY_TARGET] found=true frame=<%d> kind=<%s> visible=true box=<%s>",
                self._frame_index(page, frame),
                target_kind,
                box,
            )
            self._event(
                "verification_target",
                found=True,
                frame_index=self._frame_index(page, frame),
                frame_url=frame_url,
                target_kind=target_kind,
                visible=True,
                bounding_box=box,
            )
            await target.hover()
            await self._sleep(0.5)
            started = datetime.now(UTC)
            logger.warning("[PLAYWRIGHT_VERIFY_POINTER] action=<click_start>")
            self._event("verification_pointer", action="click_start", mode="press_hold")
            mouse = getattr(page, "mouse", None)
            if box is not None and mouse is not None:
                await mouse.move(box["x"] + box["width"] / 3, box["y"] + box["height"] / 3)
                await mouse.down()
                await self._sleep(self._click_hold_seconds)
                await mouse.up()
            else:
                await target.click(delay=250)
            ended = datetime.now(UTC)
            logger.warning("[PLAYWRIGHT_VERIFY_POINTER] action=<click_end>")
            self._event(
                "verification_pointer",
                action="click_end",
                duration_ms=(ended - started).total_seconds() * 1000,
            )
            return True
        except Exception as error:
            logger.warning("[PLAYWRIGHT_VERIFY] interaction_error=<%s>", type(error).__name__)
            self._event("verification_error", error_type=type(error).__name__)
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

    def _artifact_names(self) -> dict[str, str]:
        """Return local diagnostic artifact paths without sensitive values."""
        return {
            "events": str(self._events_path),
            "trace": str(self._trace_path),
            "video": str(self._video_path),
        }

    def _event(self, name: str, **fields: object) -> None:
        """Append one redacted structured diagnostic event to the local JSONL log."""
        if not self._diagnostics:
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "event": name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self._events_path.open("a", encoding="utf-8") as events_file:
            events_file.write(line + "\n")
        logger.warning("[PLAYWRIGHT_DIAGNOSTIC_EVENT] event=<%s>", name)

    async def _finalize_video(self, video: Video) -> None:
        """Move Patchright's generated video to the stable diagnostic filename."""
        generated_path = Path(await video.path())
        if generated_path != self._video_path and generated_path.exists():
            generated_path.replace(self._video_path)

    async def _save_debug_capture(self, page: Page, stage: str, html: str) -> None:
        """Save raw HTML and a screenshot for one challenge stage."""
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        (self._debug_dir / f"playwright_{stage}.html").write_text(
            html,
            encoding="utf-8",
        )
        await page.screenshot(path=str(self._debug_dir / f"playwright_{stage}.png"))

    def _playwright_proxy(self) -> ProxySettings | None:
        """Translate the configured proxy URL to Patchright launch settings."""
        if self._proxy_url is None:
            return None
        return cast(ProxySettings, {"server": self._proxy_url})

    def _reject(self, reason: str) -> None:
        """Raise a stable rejection marker without returning challenged content."""
        logger.warning("[PLAYWRIGHT_HANDOFF_REJECTED] reason=<%s>", reason)
        raise PlaywrightCookieFetcherError(f"[PLAYWRIGHT_HANDOFF_REJECTED] reason=<{reason}>")
