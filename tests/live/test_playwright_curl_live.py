"""Opt-in live Playwright-to-curl-cffi handoff validation."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher

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
