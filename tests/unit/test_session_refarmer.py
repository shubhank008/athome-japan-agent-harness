"""Tests for the synchronous HTTP + browser-refarm fallback orchestrator."""

from __future__ import annotations

import asyncio

import pytest

from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.session_refarmer import SessionRefarmer


class FakeScraper:
    """A minimal synchronous scraper that can block, fail, or succeed."""

    def __init__(self, *, html: str, binary: bytes, on_block: bool) -> None:
        self._html = html
        self._binary = binary
        self._on_block = on_block
        self.closed = False

    def fetch_html(self, url: str) -> str:
        """Return HTML or raise a block signal when configured."""
        if self._on_block:
            raise BlockDetected(url, "captcha")
        return self._html

    def fetch_binary(self, url: str) -> bytes:
        """Return bytes or raise a block signal when configured."""
        if self._on_block:
            raise BlockDetected(url, "403")
        return self._binary

    def close(self) -> None:
        """Record shutdown for leak checks."""
        self.closed = True


def _make_handoff(proxy_url: str = "http://proxy.example:8080") -> CookieHandoff:
    """Build a minimal valid handoff for the rebound adapter."""
    return CookieHandoff.from_browser(
        proxy_url=proxy_url,
        user_agent="Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36",
        headers={"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36"},
        cookies=[{"name": "reese84", "value": "clearance"}],
    )


def test_direct_success_never_farms() -> None:
    """A clean direct fetch returns without invoking the async farmer."""
    direct = FakeScraper(html="<h1>ok</h1>", binary=b"ok", on_block=False)
    farm_calls: list[CookieHandoff] = []

    async def run() -> str:
        refarmer = SessionRefarmer(
            build_adapter=lambda _: direct,
            farm=lambda: _completed_handoff(farm_calls),
        )
        return await refarmer.fetch_html("https://www.athome.co.jp/")

    html = asyncio.run(run())
    assert html == "<h1>ok</h1>"
    assert farm_calls == []
    assert direct.closed


async def _completed_handoff(calls: list[CookieHandoff]) -> CookieHandoff:
    """Yield a fresh handoff and record that the farmer was asked."""
    handoff = _make_handoff()
    calls.append(handoff)
    return handoff


def test_block_triggers_farm_and_rebound_succeeds() -> None:
    """A captcha block on the direct path farms then replays the rebound."""
    direct = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    rebound = FakeScraper(html="<html>recovered</html>", binary=b"ok", on_block=False)
    farm_calls: list[CookieHandoff] = []

    def build(handoff: CookieHandoff | None) -> FakeScraper:
        return direct if handoff is None else rebound

    async def run() -> str:
        refarmer = SessionRefarmer(
            build_adapter=build,
            farm=lambda: _completed_handoff(farm_calls),
        )
        return await refarmer.fetch_html("https://www.athome.co.jp/")

    html = asyncio.run(run())
    assert html == "<html>recovered</html>"
    assert len(farm_calls) == 1
    assert farm_calls[0].cookie_values == {"reese84": "clearance"}
    assert rebound.closed


def test_block_persisting_is_re_raised() -> None:
    """When the rebound still blocks, the original block propagates."""
    direct = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    rebound = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    farm_calls: list[CookieHandoff] = []

    def build(handoff: CookieHandoff | None) -> FakeScraper:
        return direct if handoff is None else rebound

    async def run() -> None:
        refarmer = SessionRefarmer(
            build_adapter=build,
            farm=lambda: _completed_handoff(farm_calls),
        )
        await refarmer.fetch_html("https://www.athome.co.jp/")

    with pytest.raises(BlockDetected):
        asyncio.run(run())
    assert len(farm_calls) == 1
    assert rebound.closed


def test_fetch_binary_recovery_path() -> None:
    """fetch_binary follows the same refarm-on-block recovery loop."""
    direct = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    rebound = FakeScraper(html="blocked", binary=b"<html>bytes</html>", on_block=False)
    farm_calls: list[CookieHandoff] = []

    def build(handoff: CookieHandoff | None) -> FakeScraper:
        return direct if handoff is None else rebound

    async def run() -> bytes:
        refarmer = SessionRefarmer(
            build_adapter=build,
            farm=lambda: _completed_handoff(farm_calls),
        )
        return await refarmer.fetch_binary("https://www.athome.co.jp/")

    data = asyncio.run(run())
    assert data == b"<html>bytes</html>"
    assert len(farm_calls) == 1


def test_multiple_refarms_close_intermediate_adapters() -> None:
    """When max_refarms >= 2, every replaced adapter is closed before reassigning."""
    direct = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    rebound1 = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    rebound2 = FakeScraper(html="<html>recovered</html>", binary=b"ok", on_block=False)

    adapters = [direct, rebound1, rebound2]
    index = 0
    farm_calls: list[CookieHandoff] = []

    def build(handoff: CookieHandoff | None) -> FakeScraper:
        nonlocal index
        if handoff is None:
            return direct
        index += 1
        return adapters[index]

    async def run() -> str:
        refarmer = SessionRefarmer(
            build_adapter=build,
            farm=lambda: _completed_handoff(farm_calls),
            max_refarms=2,
        )
        return await refarmer.fetch_html("https://www.athome.co.jp/")

    html = asyncio.run(run())
    assert html == "<html>recovered</html>"
    assert len(farm_calls) == 2
    assert direct.closed
    assert rebound1.closed
    assert rebound2.closed


def test_zero_refarms_disables_recovery() -> None:
    """With max_refarms zero the direct block is not recovered."""
    direct = FakeScraper(html="blocked", binary=b"blocked", on_block=True)
    farm_calls: list[CookieHandoff] = []

    async def run() -> None:
        refarmer = SessionRefarmer(
            build_adapter=lambda _: direct,
            farm=lambda: _completed_handoff(farm_calls),
            max_refarms=0,
        )
        await refarmer.fetch_html("https://www.athome.co.jp/")

    with pytest.raises(BlockDetected):
        asyncio.run(run())
    assert farm_calls == []
