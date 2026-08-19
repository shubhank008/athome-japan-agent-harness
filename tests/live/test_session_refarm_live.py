"""Opt-in live validation of the SessionRefarmer orchestration loop.

Scope: this file mimics how production code must fetch AtHome pages, driving
:class:`~athome_harness.scraping.session_refarmer.SessionRefarmer` end to end
(``HttpDom -> block -> PlaywrightCookie -> handoff -> HttpDom``). Playwright
browser behavior itself is covered separately in
``test_playwright_curl_live.py``.
RUN: `ATHOME_LIVE_TEST=1 pytest -sm live tests/live/test_session_refarm_live.py`
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.scraping.session_refarmer import SessionRefarmer
from athome_harness.scraping.session_state import SessionState

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.live
BROAD_SEARCH_URL = "https://www.athome.co.jp/chintai/osaka/list/"
_DETAIL_LINK = re.compile(r'href=["\']([^"\']*/chintai/[^"\']+)["\']', re.IGNORECASE)
# AtHome's broad search regularly answers in ~5s; 15s keeps the live tests
# bounded while tolerating that latency (production default is 30s).
LIVE_TIMEOUT_S = 15.0


def _absolute_urls(hrefs: Iterable[str]) -> list[str]:
    """Normalize unique detail links without following external origins."""
    urls: list[str] = []
    for href in hrefs:
        if href.startswith("/"):
            href = f"https://www.athome.co.jp{href}"
        if href.startswith("https://www.athome.co.jp/") and href not in urls:
            urls.append(href)
    return urls


def _build_refarmer(debug_dir: Path) -> SessionRefarmer:
    """Wire the orchestrator exactly as production code would."""
    budgets = Budgets(http_timeout_s=LIVE_TIMEOUT_S)

    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
        return HttpDomAdapter(budgets, handoff=handoff)

    async def farm() -> CookieHandoff:
        return await PlaywrightCookieFetcher(
            url=BROAD_SEARCH_URL,
            debug_dir=debug_dir,
        ).farm()

    return SessionRefarmer(build_adapter=build_adapter, farm=farm)


@pytest.mark.asyncio
async def test_refarmer_direct_path_fetches_live_listing(tmp_path: Path) -> None:
    """The cheap curl-cffi path wins when AtHome answers without a block."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    refarmer = _build_refarmer(tmp_path)
    html = await refarmer.fetch_html(BROAD_SEARCH_URL)
    #print(len(html))

    assert isinstance(html, str)
    assert detect_athome_challenge(html) is None
    assert len(html) > 200
    logger.warning("[REHANDOFF_LIVE_DIRECT] html_chars=<%d>", len(html))


@pytest.mark.asyncio
async def test_refarmer_recovers_after_forced_block(tmp_path: Path) -> None:
    """A first block forces a browser farm, and the rebound session recovers."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    def build_adapter(handoff: object) -> HttpDomAdapter:
        return _BlockOnceAdapter(Budgets(http_timeout_s=LIVE_TIMEOUT_S), handoff=handoff)  # type: ignore[arg-type]

    async def farm() -> CookieHandoff:
        return await PlaywrightCookieFetcher(url=BROAD_SEARCH_URL, debug_dir=tmp_path).farm()

    forced_refarmer = SessionRefarmer(build_adapter=build_adapter, farm=farm)
    html = await forced_refarmer.fetch_html(BROAD_SEARCH_URL)

    assert isinstance(html, str)
    assert detect_athome_challenge(html) is None
    assert len(html) > 200

    session_path = tmp_path / "session_state.json"
    assert session_path.exists()
    session = SessionState.load(session_path)
    assert session.cookies
    logger.warning(
        "[REHANDOFF_LIVE_RECOVERED] cookies=<%d> chrome_major=<%s>",
        len(session.cookies),
        session.chrome_major_version,
    )


class _BlockOnceAdapter(HttpDomAdapter):
    """Adapter whose first fetch is blocked so the refarm loop engages.

    Real blocks are issued by AtHome, not by us; this fixture forces exactly
    one :class:`BlockDetected` so the live orchestration path (farm, rebind,
    retry) is exercised deterministically even when AtHome lets the direct
    request through.
    """

    def __init__(self, budgets: Budgets, *, handoff: CookieHandoff | None) -> None:
        super().__init__(budgets, handoff=handoff)
        if handoff is None:
            self.blocked_once = False
        else:
            self.blocked_once = True

    def fetch_html(self, url: str) -> str:
        if not self.blocked_once:
            self.blocked_once = True
            raise BlockDetected(url, "403")
        return super().fetch_html(url)


@pytest.mark.asyncio
async def test_refarmer_fetches_detail_pages_through_farmed_session(tmp_path: Path) -> None:
    """Multi-page worker flow: one farm, several detail pages via curl-cffi."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    #print(tmp_path)

    refarmer = _build_refarmer(tmp_path)
    broad_html = await refarmer.fetch_html(BROAD_SEARCH_URL)
    detail_urls = _absolute_urls(_DETAIL_LINK.findall(broad_html))[:3]
    #print(detail_urls)

    if len(detail_urls) < 3:
        pytest.fail("AtHome broad search did not expose three detail links")

    details = [parse_detail_page(await refarmer.fetch_html(url)) for url in detail_urls]
    assert len(details) == 3
    assert all(detail.internal_id for detail in details)
    logger.warning("[REHANDOFF_LIVE_MULTIPAGE] pages=<3>")
