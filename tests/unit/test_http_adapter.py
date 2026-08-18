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
from athome_harness.scraping.http_adapter import (
    HttpDomAdapter,
    _detect_athome_challenge,
    _detect_signature,
)

# Redacted form of the fixture URL used across tests; the marker contract never
# logs full URLs, so assertions use the redacted variant.
REDACTED = "https://www.athome.co.jp/list"

# A plausible URL with a query string and credentials to prove redaction.
FULL_URL = "https://user:secret@www.athome.co.jp/list/?PAGENO=2&x=1"

# Inline representative AtHome challenge bodies, exactly as captured in the M3
# incident: AtHome answers HTTP 200 with a puzzle/auth page.
PUZZLE_BODY = '<html><body><h1>Click to verify</h1><p>For security...</p></body></html>'
JAPANESE_PUZZLE_BODY = "<html><body><h1>認証にご協力ください</h1></body></html>"
JAVASCRIPT_BODY = (
    "<html><body>To regain access, please make sure that "
    "cookies and JavaScript are enabled.</body></html>"
)
UPPER_PUZZLE_BODY = "<html><body><h1>CLICK TO VERIFY</h1></body></html>"


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


@pytest.mark.parametrize(
    "signature,status,body",
    [
        (None, 200, "<html>ok</html>"),
        ("403", 403, "<html>denied</html>"),
        ("429", 429, "<html>slow down</html>"),
        ("captcha", 200, "<html>reCAPTCHA</html>"),
        ("captcha", 403, "<html>verify you are human</html>"),
        ("captcha", 200, PUZZLE_BODY),
        ("captcha", 200, JAPANESE_PUZZLE_BODY),
        ("captcha", 200, JAVASCRIPT_BODY),
        ("captcha", 403, JAVASCRIPT_BODY),
    ],
)
def test_detect_signature(signature, status, body) -> None:
    """The block-signature mapper honors the documented signals."""
    assert _detect_signature(status, body) == signature


@pytest.mark.parametrize(
    "kind,body",
    [
        ("puzzle", PUZZLE_BODY),
        ("puzzle", JAPANESE_PUZZLE_BODY),
        ("puzzle", UPPER_PUZZLE_BODY),
        ("javascript", JAVASCRIPT_BODY),
    ],
)
def test_detect_athome_challenge(kind, body) -> None:
    """Precise AtHome challenge markers map to the documented kinds."""
    assert _detect_athome_challenge(body) == kind


@pytest.mark.parametrize(
    "body",
    [
        "<html>ok</html>",
        "<html>reCAPTCHA here</html>",
        "click here to verify your email address",
        "enable JavaScript for this widget",
    ],
)
def test_detect_athome_challenge_rejects_normal_pages(body) -> None:
    """Non-challenge pages, near-misses, and generic captchas are not challenges."""
    assert _detect_athome_challenge(body) is None


def test_fetch_html_returns_decoded_text() -> None:
    """A 200 response body with HTML text comes back decoded."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(200, text="<html>居室</html>"))
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
        assert proxy.proxies == []


def test_proxy_exhausted_raises_block_detected() -> None:
    """When the provider runs out of proxies, the block is surfaced."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(side_effect=[httpx.Response(403), httpx.Response(403)])
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


def test_challenge_200_raises_block_detected_without_proxy() -> None:
    """A 200 puzzle page is a captcha block when there is no proxy provider."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(200, text=PUZZLE_BODY))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
    assert excinfo.value.signature == "captcha"


def test_challenge_marker_redacts_url_and_kind(caplog) -> None:
    """The [ATHOME_CHALLENGE] marker carries a redacted URL and the kind."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(200, text=JAPANESE_PUZZLE_BODY))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected):
            adapter.fetch_html(FULL_URL)
    messages = [r.getMessage() for r in caplog.records]
    matches = [m for m in messages if "[ATHOME_CHALLENGE]" in m]
    assert len(matches) == 1
    assert "kind=<puzzle>" in matches[0]
    # redact_url drops the userinfo and the query string but keeps the path.
    assert "url=<https://www.athome.co.jp/list/>" in matches[0]
    assert "secret" not in matches[0]
    assert "PAGENO" not in matches[0]
    assert "user:" not in matches[0]


def test_challenge_javascript_kind_marker(caplog) -> None:
    """The JavaScript/cookie interstitial is marked kind=javascript."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        router.get("/list/").mock(return_value=httpx.Response(200, text=JAVASCRIPT_BODY))
        adapter, _ = make_adapter()
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
    assert excinfo.value.signature == "captcha"
    messages = [r.getMessage() for r in caplog.records]
    marker = "[ATHOME_CHALLENGE] url=<https://www.athome.co.jp/list/> kind=<javascript>"
    assert any(marker in m for m in messages)


def test_challenge_proxy_recovery(caplog) -> None:
    """A challenge rotates to a proxy and recovers without solving the puzzle."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(
            side_effect=[
                httpx.Response(200, text=PUZZLE_BODY),
                httpx.Response(200, text="<html>recovered</html>"),
            ]
        )
        proxy = StubProxy(["http://proxy.invalid:8080"])
        adapter, _ = make_adapter(proxy_provider=proxy)
        assert adapter.fetch_html("https://www.athome.co.jp/list/") == "<html>recovered</html>"
        assert len(route.calls) == 2
    messages = [r.getMessage() for r in caplog.records]
    marker = "[ATHOME_CHALLENGE] url=<https://www.athome.co.jp/list/> kind=<puzzle>"
    assert any(marker in m for m in messages)
    assert any("[PROXY_ROTATE]" in m for m in messages)
    assert any("[PROXY_RECOVERED]" in m for m in messages)


def test_challenge_exhausted_retries(caplog) -> None:
    """When every bounded alternate request hits a challenge, BlockDetected surfaces."""
    with respx.mock(base_url="https://www.athome.co.jp") as router:
        route = router.get("/list/").mock(
            side_effect=[
                httpx.Response(200, text=PUZZLE_BODY),
                httpx.Response(200, text=JAPANESE_PUZZLE_BODY),
            ]
        )
        proxy = StubProxy(["http://proxy.invalid:8080"])
        adapter, _ = make_adapter(proxy_provider=proxy)
        with pytest.raises(BlockDetected) as excinfo:
            adapter.fetch_html("https://www.athome.co.jp/list/")
        assert len(route.calls) == 2
    assert excinfo.value.signature == "captcha"
    messages = [r.getMessage() for r in caplog.records]
    challenges = [m for m in messages if "[ATHOME_CHALLENGE]" in m]
    assert len(challenges) == 2


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
