"""Opt-in live Playwright-to-curl-cffi handoff validation."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.scraping.session_refarmer import SessionRefarmer
from athome_harness.scraping.session_state import SessionState

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.live
BROAD_SEARCH_URL = "https://www.athome.co.jp/chintai/osaka/list/"
_DETAIL_LINK = re.compile(r'href=["\']([^"\']*/chintai/[^"\']+)["\']', re.IGNORECASE)


def _absolute_urls(hrefs: Iterable[str]) -> list[str]:
    """Normalize unique detail links without following external origins."""
    urls: list[str] = []
    for href in hrefs:
        if href.startswith("/"):
            href = f"https://www.athome.co.jp{href}"
        if href.startswith("https://www.athome.co.jp/") and href not in urls:
            urls.append(href)
    return urls


@pytest.mark.asyncio
async def test_playwright_handoff_fetches_four_live_pages() -> None:
    """Farm one browser session and reuse it for four curl-cffi pages."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    handoff = await PlaywrightCookieFetcher(url=BROAD_SEARCH_URL).farm()
    adapter = HttpDomAdapter(Budgets(http_timeout_s=2.0), handoff=handoff)
    try:
        broad_html = adapter.fetch_html(BROAD_SEARCH_URL)
        detail_urls = _absolute_urls(_DETAIL_LINK.findall(broad_html))[:3]
        if len(detail_urls) < 3:
            pytest.fail("AtHome broad search did not expose three detail links")
        details = [parse_detail_page(adapter.fetch_html(url)) for url in detail_urls]
    finally:
        adapter.close()

    assert len(details) == 3
    assert all(detail.internal_id for detail in details)
    logger.warning("[CURL_PLAYWRIGHT_INTEGRATION] pages=<3> timeout=<2>")


@pytest.mark.asyncio
async def test_refarmer_recovery_fetches_after_challenge() -> None:
    """HttpDom block is recovered by farming and rebinding a browser session."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    budgets = Budgets(http_timeout_s=2.0)
    with TemporaryDirectory() as debug:
        debug_dir = Path(debug)

        def build_adapter(handoff: object) -> HttpDomAdapter:
            return HttpDomAdapter(budgets, handoff=handoff)

        async def farm() -> object:
            return await PlaywrightCookieFetcher(
                url=BROAD_SEARCH_URL,
                debug_dir=debug_dir,
                wait_seconds=0,
            ).farm()

        refarmer = SessionRefarmer(
            build_adapter=build_adapter,
            farm=farm,
        )
        html = await refarmer.fetch_html(BROAD_SEARCH_URL)

    assert isinstance(html, str)
    assert detect_athome_challenge(html) is None
    assert len(html) > 200

    session_path = debug_dir / "session_state.json"
    assert session_path.exists()
    session = SessionState.load(session_path)
    assert session.cookies
    logger.warning(
        "[REHANDOFF_LIVE_RECOVERED] cookies=<%d> chrome_major=<%s>",
        len(session.cookies),
        session.chrome_major_version,
    )
