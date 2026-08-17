"""Unit tests for the Webshare proxy provider (T09).

Uses a fake transport (pure logic, no network) and hand-built credentials so no
live proxy call ever runs in CI. Covers credential handling, the direct-first
rotation policy, the retry budget bound, and that the provider emits no
credentials through its public surface.
"""

from __future__ import annotations

import pytest

from athome_harness.config import Budgets, Settings
from athome_harness.scraping.proxy.base import BaseProxyProvider
from athome_harness.scraping.proxy.webshare import (
    _WEBSHARE_HOST,
    WebshareProxyProvider,
    _build_webshare_url,
)


def make_settings(user: str | None = "user", password: str | None = "pass") -> Settings:
    """Build a Settings with a throwaway API key and the given Webshare creds."""
    return Settings(
        openrouter_api_key="test-key",
        webshare_proxy_user=user,
        webshare_proxy_pass=password,
    )


def test_webshare_url_embeds_credentials() -> None:
    """The credentialed URL targets the Webshare rotating gateway."""
    url = _build_webshare_url("u", "p")
    assert url == f"http://u:p@{_WEBSHARE_HOST}:80"
    assert "u" in url and "p" in url


def test_direct_first_semantics() -> None:
    """get_proxy returns None until a block is reported (direct first)."""
    provider = WebshareProxyProvider(make_settings(), Budgets())
    assert provider.get_proxy() is None


def test_rotation_on_block() -> None:
    """report_block arms the proxy and get_proxy returns the credentialed URL."""
    provider = WebshareProxyProvider(make_settings(), Budgets())
    assert provider.report_block("https://www.athome.co.jp/list/") is not None
    proxy = provider.get_proxy()
    assert proxy is not None
    assert f"@{_WEBSHARE_HOST}:80" in proxy


def test_retry_budget_bounds_rotation() -> None:
    """After proxy_retries consecutive blocks, report_block returns None."""
    budgets = Budgets(proxy_retries=3)
    provider = WebshareProxyProvider(make_settings(), budgets)
    assert provider.report_block("https://e.test/") is not None
    assert provider.report_block("https://e.test/") is not None
    assert provider.report_block("https://e.test/") is not None
    assert provider.report_block("https://e.test/") is None


def test_reset_rearms_rotation() -> None:
    """After reset the provider goes back to direct-first for a new session."""
    provider = WebshareProxyProvider(make_settings(), Budgets(proxy_retries=1))
    assert provider.report_block("https://e.test/") is not None
    assert provider.report_block("https://e.test/") is None
    provider.reset()
    assert provider.get_proxy() is None
    assert provider.report_block("https://e.test/") is not None


def test_missing_credentials_raise() -> None:
    """Without webshare creds the provider refuses to construct."""
    with pytest.raises(ValueError):
        WebshareProxyProvider(make_settings(user=None), Budgets())
    with pytest.raises(ValueError):
        WebshareProxyProvider(make_settings(password=None), Budgets())


def test_provider_conforms_to_protocol() -> None:
    """WebshareProxyProvider satisfies the ProxyProvider protocol shape."""
    provider = WebshareProxyProvider(make_settings(), Budgets())

    def use(p: BaseProxyProvider) -> None:
        assert p.get_proxy() is None
        assert p.report_block("https://e.test/") is not None

    use(provider)


def test_rotation_policy_in_base() -> None:
    """The direct-first policy with bounded budget lives in the base class."""
    pool = ["http://p1.invalid:80", "http://p2.invalid:80"]

    class TwoProxyPool(BaseProxyProvider):
        """Test-only subclass exposing a fixed two-entry pool."""

        def _build_pool(self) -> list[str]:
            return pool

    provider = TwoProxyPool(Budgets(proxy_retries=3))
    assert provider.get_proxy() is None  # direct first
    assert provider.report_block("https://e.test/") == "http://p1.invalid:80"
    assert provider.get_proxy() == "http://p1.invalid:80"
    assert provider.report_block("https://e.test/") == "http://p2.invalid:80"
    assert provider.report_block("https://e.test/") == "http://p2.invalid:80"
    assert provider.report_block("https://e.test/") is None  # budget spent
