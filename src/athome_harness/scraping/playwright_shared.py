"""Shared Patchright browser primitives used by the farmer and the probe.

The production :class:`~athome_harness.scraping.playwright_cookie_fetcher.PlaywrightCookieFetcher`
and the operator probe (``scripts/playwright_manual_probe.py``) drive the same
AtHome pages, so the mechanics for settling a page live here once:

* route interception that aborts ad/analytics traffic,
* a selector race that waits for whichever attaches first: the WAF challenge
  box or the listing content,
* a bounded retry around ``page.content()`` for late navigations,
* CapSolver puzzle-solvers shared by both click and API solve modes.

Diagnostic-heavy behavior (screenshots, video, tracing, JSONL events) stays
in the probe; this module only owns page-settling mechanics.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final

import aiohttp
from patchright.async_api import ElementHandle, Page, Route

logger = logging.getLogger(__name__)

# --- Selectors for waiting on either main content or WAF challenge load ---
CHALLENGE_SELECTOR: Final = "#captcha-box"
LISTING_SELECTOR: Final = "#container, .maincontents"
COMBINED_TARGET: Final = f"{CHALLENGE_SELECTOR}, {LISTING_SELECTOR}"

DEFAULT_SELECTOR_TIMEOUT_MS: Final = 30_000
DEFAULT_SETTLE_SECONDS: Final = 0.5
CONTENT_READ_ATTEMPTS: Final = 4
CONTENT_RETRY_DELAY_SECONDS: Final = 1.0

# Curated from AtHome's network waterfall; aborting these trackers saves
# page-load time and removes navigation noise that races content reads.
ROUTE_BLOCKED_SUBSTRINGS: Final = (
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "doubleclick.net",
    "amazon-adsystem.com",
    "googlesyndication.com",
    "adservice.google",
    "scorecardresearch.com",
    "criteo.com",
    "amzn.js",  # Amazon ad library
    "pubmatic.com",
    "google.com/ccm/",
    "google.com/pagead/",
)

_route_log: Callable[[str], None] | None = None


def set_route_logger(log_fn: Callable[[str], None] | None) -> None:
    """Install an optional sink for blocked-route notices (probe DEBUG mode)."""
    global _route_log
    _route_log = log_fn


async def intercept_route(route: Route) -> None:
    """Abort known tracker/ad requests and let all core assets through.

    Fail-safe by design: when the interception logic itself raises, the
    request is still allowed through so one bad URL cannot break the whole
    Playwright pipeline.
    """
    try:
        url = route.request.url.lower()
        for domain in ROUTE_BLOCKED_SUBSTRINGS:
            if domain in url:
                if _route_log is not None:
                    _route_log(f"Abort loading of resources from: {domain}")
                await route.abort()
                return
        # Allow all core structural assets (HTML, internal JS, CSS).
        await route.continue_()
    except Exception:
        # Fail-safe: never let the interceptor break the browser pipeline.
        try:
            await route.continue_()
        except Exception:
            logger.debug("[ROUTE_INTERCEPT] continue_ failed after error", exc_info=True)


async def wait_for_page_signal(
    page: Page,
    *,
    timeout_ms: int = DEFAULT_SELECTOR_TIMEOUT_MS,
) -> ElementHandle | None:
    """Wait until either the challenge box or the listing content attaches.

    This is the deterministic settle condition for AtHome pages: after
    ``domcontentloaded`` a late client-side redirect may still be in flight,
    and a blind fixed sleep both wastes time and races the content read.
    Waiting for one of the two terminal selectors means the page has decided
    which branch it is on. Returns the winning element handle, or ``None``
    when neither selector appeared within the timeout (the caller proceeds
    with whatever rendered).
    """
    try:
        return await page.wait_for_selector(COMBINED_TARGET, state="attached", timeout=timeout_ms)
    except Exception as error:
        logger.warning(
            "[PLAYWRIGHT_SELECTOR_TIMEOUT] selector=<%s> timeout_ms=<%d> error=<%s>",
            COMBINED_TARGET,
            timeout_ms,
            type(error).__name__,
        )
        return None


async def read_settled_content(
    page: Page,
    *,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    attempts: int = CONTENT_READ_ATTEMPTS,
    retry_delay_seconds: float = CONTENT_RETRY_DELAY_SECONDS,
) -> str:
    """Read the page HTML, retrying briefly when a navigation races the read.

    ``wait_for_page_signal`` is the primary defense against racing a late
    navigation; this helper is the last line of defense when one slips
    through anyway. A bounded retry after a short settle delay resolves the
    race without unbounded waits.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    sleep = sleep_fn or asyncio.sleep
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await page.content()
        except Exception as error:
            last_error = error
            logger.warning(
                "[PLAYWRIGHT_CONTENT_RETRY] attempt=<%d/%d> error=<%s>",
                attempt + 1,
                attempts,
                type(error).__name__,
            )
            await sleep(retry_delay_seconds)
    assert last_error is not None
    raise last_error


async def solve_turnstile_capsolver(api_key: str, site_url: str, site_key: str) -> str | None:
    """Solve a Cloudflare Turnstile challenge via the CapSolver API."""
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


async def solve_geetest_capsolver(
    api_key: str, site_url: str, gt: str, challenge: str
) -> dict[str, object] | None:
    """Solve a Geetest V3 puzzle via the CapSolver API.

    Returns the solution mapping (``challenge``/``validate``/``seccode``) or
    ``None`` when CapSolver could not solve it.
    """
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
