"""Contract tests for the Playwright adapter scaffold (T08).

Proves that :class:`PlaywrightAdapter` satisfies the :class:`BaseScraper`
interface (so it is a drop-in swap for :class:`HttpDomAdapter` later), and that
its operation methods fail loudly with a clear, stable message instead of doing
partial or silent work.
"""

from __future__ import annotations

import pytest

from athome_harness.scraping.base import BaseScraper
from athome_harness.scraping.playwright_adapter import PlaywrightAdapter


def test_playwright_adapter_conforms_to_base_scraper() -> None:
    """A function typed against BaseScraper can accept the Playwright adapter."""
    scraper: BaseScraper = PlaywrightAdapter()
    assert isinstance(scraper, BaseScraper)


def test_fetch_html_raises_not_implemented() -> None:
    """fetch_html fails loudly with the scaffold message."""
    adapter = PlaywrightAdapter()
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.fetch_html("https://www.athome.co.jp/list/")
    assert "scaffold" in str(excinfo.value)


def test_fetch_binary_raises_not_implemented() -> None:
    """fetch_binary fails loudly with the scaffold message."""
    adapter = PlaywrightAdapter()
    with pytest.raises(NotImplementedError) as excinfo:
        adapter.fetch_binary("https://www.athome.co.jp/list/")
    assert "scaffold" in str(excinfo.value)
