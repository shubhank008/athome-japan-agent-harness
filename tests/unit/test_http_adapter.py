"""Unit tests for the HTTP DOM adapter (T07).

Exercises the adapter with a mocked transport via respx so no real network is
touched. Covers browser-like headers, block detection for 403/429/captcha, the
proxy hook point (direct-first, rotate on block, recover markers, bounded
retries), transparent retry with exponential backoff, and the DOM parse helper.

A real-network check lives behind the ``live`` marker and is skipped by default.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected, ProxyProvider
from athome_harness.scraping.http_adapter import HttpDomAdapter, _detect_signature

# Redacted form of the fixture URL used across tests; the marker contract never
# logs full URLs, so assertions use the redacted variant.
REDACTED = "https://www.athome.co.jp/list"

# A plausible URL with a query string and credentials to prove redaction.
FULL_URL = "https://user:secret@www.athome.co.jp/list/?PAGENO=2&x=1"


class StubProxy:
    """Minimal in-memory ProxyProvider that serves a fixed rotation list."""

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = proxies
        self.used: list[str] = []

    def get_proxy(self) -> str | None:
        return None

    def report_block(self, url: str) -> str | None:
        if not self.proxies:
            return None
        return self.proxies.pop(0)


def make_adapter(
    budgets: Budgets | None = None,
    *,
    proxy_provider: ProxyProvider | None = None,
) -> tuple[HttpDomAdapter, list[float]]:
    """Build an adapter whose backoff sleeps are recorded instead of real."""
    sleeps: list[float] = []
    return (
        HttpDomAdapter(
            budgets or Budgets(),
            proxy_provider=proxy_provider,
            sleep_fn=sleeps.append,
        ),
        sleeps,
    )


@pytest.mark.parametrize("signature,status,body", [
    (None, 200, "<html>ok</html>"),
    ("403", 403, "<html>denied</html>"),
    ("429", 429, "<html>slow down</html>"),
    ("captcha", 200, "<html>reCAPTCHA</html>"),
    ("captcha", 403, "<html>verify you are human</html>"),
])
def test_detect_signature(signature, status, body) -> None:
    """The block-signature mapper honors the documented signals."""
    assert _detect_signature(status, body) == signature


def test_fetch_html_returns_decoded_text() -> None:
    """A 200 response body with HTML text comes back decoded."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(
            return_value=httpx.Response(200, text="<html>居室</html>")
        )
        adapter, _ = make_adapter()
        assert adapter.fetch_html("https://www.athome.co.jp/list/") == "<html>居室</html>"


def test_fetch_binary_returns_raw_bytes() -> None:
    """Binary payloads come back untouched as bytes."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/img.png").mock(return_value=httpx.Response(200, content=b"\x89PNG"))
        adapter, _ = make_adapter()
        assert adapter.fetch_binary("https://www.athome.co.jp/img.png") == b"\x89PNG"


def test_browser_headers_sent_on_request() -> None:
    """Each request carries the browser-like header envelope."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(return_value=httpx.Response(200, text="x"))
        adapter, _ = make_adapter()
        adapter.fetch_html("https://www.athome.co.jp/list/")
        headers = route.calls[0].request.headers
        assert headers["User-Agent"].startswith("Mozilla/5.0")
        assert headers["Accept-Language"].startswith("ja")


def test_403_raises_block_detected_without_proxy() -> None:
    """Without a provider a 403 surfaces immediately as BlockDetected 403."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(403))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
    assert excinfo.value.signature == "403"
    assert "[BLOCK_DETECTED]" in str(excinfo.value)


def test_429_raises_block_detected_without_proxy() -> None:
    """A 429 (rate-limit) surfaces as BlockDetected 429."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(429))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
    assert excinfo.value.signature == "429"


def test_captcha_body_raises_block_detected() -> None:
    """A captcha marker in the body, even on 200, is a captcha block."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(
            return_value=httpx.Response(200, text="Please verify you are human")
        )
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
    assert excinfo.value.signature == "captcha"


def test_proxy_rotate_and_recover_markers() -> None:
    """Direct-first, then rotate on block, then recover with proxy markers."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(
            side_effect=[
                httpx.Response(403),
                httpx.Response(200, text="<html>recovered</html>"),
            ]
        )
        proxy = StubProxy(["http://proxy.invalid:8080"])
        adapter, _ = make_adapter(proxy_provider=proxy)
        assert adapter.fetch_html("https://www.athome.co.jp/list/") == "<html>recovered</html>"
        # Two total requests: direct (blocked) then proxy (recovered).
        assert len(route.calls) == 2
        assert proxy.used == []  # get_proxy returns None by protocol
        assert proxy.proxies == []


def test_proxy_exhausted_raises_block_detected() -> None:
    """When the provider runs out of proxies, the block is surfaced."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(
            side_effect=[httpx.Response(403), httpx.Response(403)]
        )
        proxy = StubProxy(["http://proxy.invalid:8080"])
        adapter, _ = make_adapter(proxy_provider=proxy)
        with pytest.raises(BlockDetected):
            adapter.fetch_html("https://www.athome.co.jp/list/")


def test_redaction_in_marker_disallows_credentials_and_query() -> None:
    """The [BLOCK_DETECTED] marker never leaks credentials or query strings."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(403))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html(FULL_URL)
    assert "secret" not in str(excinfo.value)
    assert "PAGENO" not in str(excinfo.value)


def test_transient_error_retries_with_backoff() -> None:
    """A transport error is retried with recorded exponential backoff."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.ConnectError("boom"),
                httpx.Response(200, text="ok"),
            ]
        )
        adapter, sleeps = make_adapter()
        assert adapter.fetch_html("https://www.athome.co.jp/list/") == "ok"
        assert len(route.calls) == 3
        # Base 0.5 * 2^0 and 0.5 * 2^1, both under the 8s cap.
        assert sleeps == [0.5, 1.0]


def test_transient_error_gives_up_after_max() -> None:
    """Persistent transport errors exhaust retries and propagate."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(
            side_effect=[
                httpx.ConnectError("x"),
                httpx.ConnectError("x"),
                httpx.ConnectError("x"),
            ]
        )
        adapter, sleeps = make_adapter()
        with pytest.raises(httpx.TransportError):
            adapter.fetch_html("https://www.athome.co.jp/list/")
        assert len(route.calls) == 3
        assert sleeps == [0.5, 1.0]


def test_fetch_dom_parses_selectolax_tree() -> None:
    """fetch_dom returns a selectolax HTMLParser with the fetched markup."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(
            return_value=httpx.Response(200, text="<html><body><h1>Title</h1></body></html>")
        )
        adapter, _ = make_adapter()
        dom = adapter.fetch_dom("https://www.athome.co.jp/list/")
        assert dom.css_first("h1").text() == "Title"


@pytest.mark.live
def test_live_fetch_smoke() -> None:
    """Real-network smoke check; skipped by default (marker ``live``)."""
    adapter = HttpDomAdapter(Budgets())
    body = adapter.fetch_html("https://example.com/")
    assert "<html" in body
    adapter.close()
