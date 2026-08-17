"""Contract tests for the scraper base abstractions (T05).

Proves the :class:`BaseScraper` / :class:`BlockDetected` /
:class:`ProxyProvider` contract with an in-memory fake adapter so no network and
no third-party HTTP import leaks into business logic.
"""

from __future__ import annotations

import pytest

from athome_harness.scraping.base import (
    BaseScraper,
    BlockDetected,
    ProxyProvider,
    redact_url,
)


class FakeScraper(BaseScraper):
    """In-memory adapter used to prove the contract is implementable."""

    def fetch_html(self, url: str) -> str:
        return f"<html>{url}</html>"

    def fetch_binary(self, url: str) -> bytes:
        return url.encode("utf-8")


class FakeProxy:
    """In-memory ProxyProvider that hands out a static URL after a block."""

    def __init__(self) -> None:
        self.block_reports = 0

    def get_proxy(self) -> str | None:
        return None

    def report_block(self, url: str) -> str | None:
        self.block_reports += 1
        return "http://proxy.invalid:8080"


def test_fake_adapter_implements_contract() -> None:
    """A subclass with both fetch methods is a usable BaseScraper."""
    scraper = FakeScraper()
    assert isinstance(scraper, BaseScraper)
    assert scraper.fetch_html("https://e.test/a") == "<html>https://e.test/a</html>"
    assert scraper.fetch_binary("x") == b"x"


def test_base_scraper_is_abstract() -> None:
    """BaseScraper cannot be instantiated directly; it is a pure interface."""
    with pytest.raises(TypeError):
        BaseScraper()  # type: ignore[abstract]  # self-test of the abstractness


def test_block_detected_carries_signature() -> None:
    """A block carries its detection signature verbatim."""
    exc = BlockDetected("https://www.athome.co.jp/list/", "429")
    assert exc.signature == "429"
    assert isinstance(exc, Exception)


@pytest.mark.parametrize("signature", ["403", "429", "captcha"])
def test_block_detected_accepts_known_signatures(signature: str) -> None:
    """Each documented signature is representable."""
    exc = BlockDetected("https://e.test/", signature)  # type: ignore[arg-type]
    assert exc.signature == signature


def test_block_detected_marker_is_redacted() -> None:
    """The [BLOCK_DETECTED] marker strips query strings and credentials."""
    url = "https://user:secret@www.athome.co.jp/list/?PAGENO=2&x=1"
    exc = BlockDetected(url, "403")
    assert str(exc) == ("[BLOCK_DETECTED] url=<https://www.athome.co.jp/list/> signature=<403>")
    assert "secret" not in str(exc)
    assert "PAGENO" not in str(exc)


def test_proxy_provider_protocol_conformance() -> None:
    """Any object with the proxy methods satisfies the ProxyProvider protocol."""
    proxy = FakeProxy()

    def use(p: ProxyProvider) -> None:
        assert p.get_proxy() is None
        assert p.report_block("https://e.test/") == "http://proxy.invalid:8080"

    use(proxy)
    assert proxy.block_reports == 1


def test_redact_url_drops_query_and_credentials() -> None:
    """The standalone redaction helper keeps only scheme, host, and path."""
    assert redact_url("https://a:b@host.invalid/p?q=1") == "https://host.invalid/p"
    assert redact_url("https://host.invalid/p?q=1") == "https://host.invalid/p"
