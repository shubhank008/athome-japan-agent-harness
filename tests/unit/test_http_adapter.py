"""Unit tests for the curl-cffi HTTP DOM adapter."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from curl_cffi import requests as curl_requests

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected, ProxyProvider
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.http_adapter import (
    BROWSER_HEADERS,
    HttpDomAdapter,
    _detect_athome_challenge,
    _detect_signature,
)

REDACTED = "https://www.athome.co.jp/list"
FULL_URL = "https://user:secret@www.athome.co.jp/list/?PAGENO=2&x=1"

# Inline representative AtHome challenge bodies, exactly as captured in the M3
# incident: AtHome answers HTTP 200 with a puzzle/auth page.
PUZZLE_BODY = "<html><body><h1>Click to verify</h1><p>For security...</p></body></html>"
JAPANESE_PUZZLE_BODY = "<html><body><h1>認証にご協力ください</h1></body></html>"
JAVASCRIPT_BODY = (
    "<html><body>To regain access, please make sure that "
    "cookies and JavaScript are enabled.</body></html>"
)


@dataclass
class FakeResponse:
    """Small response object matching the adapter's curl response protocol."""

    status_code: int = 200
    text: str = "ok"

    @property
    def content(self) -> bytes:
        """Return the response text as UTF-8 bytes."""
        return self.text.encode("utf-8")


class FakeSession:
    """Deterministic curl session that records request kwargs."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        """Return the next configured response or raise its transport error."""
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        """Record that the session was closed."""
        self.closed = True


class StubProxy:
    """Minimal in-memory ProxyProvider that serves a fixed rotation list."""

    def __init__(self, proxies: list[str]) -> None:
        self.proxies = proxies
        self.reported: list[str] = []

    def get_proxy(self) -> str | None:
        """The adapter starts directly and does not need this hook."""
        return None

    def report_block(self, url: str) -> str | None:
        """Return the next proxy and record the blocked URL."""
        self.reported.append(url)
        if not self.proxies:
            return None
        return self.proxies.pop(0)


def make_adapter(
    responses: list[FakeResponse | Exception],
    budgets: Budgets | None = None,
    *,
    proxy_provider: ProxyProvider | None = None,
) -> tuple[HttpDomAdapter, FakeSession, list[float]]:
    """Build an adapter with a deterministic injected curl session."""
    sleeps: list[float] = []
    session = FakeSession(responses)
    adapter = HttpDomAdapter(
        budgets or Budgets(),
        proxy_provider=proxy_provider,
        client=session,
        sleep_fn=sleeps.append,
    )
    return adapter, session, sleeps


def make_handoff() -> CookieHandoff:
    """Build a representative browser handoff without real secrets."""
    return CookieHandoff.from_browser(
        proxy_url="http://proxy-user:proxy-pass@proxy.example:8080",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0.0.0",
        headers={"User-Agent": "captured-agent", "Accept-Language": "ja-JP"},
        cookies=[{"name": "reese84", "value": "clearance-value"}],
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
    ],
)
def test_detect_signature(signature, status, body) -> None:
    """The block-signature mapper honors all documented signals."""
    assert _detect_signature(status, body) == signature


@pytest.mark.parametrize(
    "kind,body",
    [
        ("puzzle", PUZZLE_BODY),
        ("puzzle", JAPANESE_PUZZLE_BODY),
        ("javascript", JAVASCRIPT_BODY),
    ],
)
def test_detect_athome_challenge(kind, body) -> None:
    """Precise AtHome challenge markers map to their documented kinds."""
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
    """Near-misses and generic captchas are not AtHome challenge markers."""
    assert _detect_athome_challenge(body) is None


def test_fetch_html_returns_decoded_text() -> None:
    """A successful response body comes back as decoded HTML."""
    adapter, session, _ = make_adapter([FakeResponse(text="<html>居室</html>")])
    assert adapter.fetch_html(REDACTED) == "<html>居室</html>"
    assert session.calls[0][1]["impersonate"] == "chrome"


def test_fetch_binary_returns_raw_bytes() -> None:
    """Binary payloads come back untouched as bytes."""
    adapter, _, _ = make_adapter([FakeResponse(text="PNG")])
    assert adapter.fetch_binary(REDACTED) == b"PNG"


def test_browser_headers_and_timeout_sent_on_request() -> None:
    """Each request carries browser headers, profile, and configured timeout."""
    budgets = Budgets(http_timeout_s=2.0)
    adapter, session, _ = make_adapter([FakeResponse()], budgets)
    adapter.fetch_html(REDACTED)
    options = session.calls[0][1]
    assert options["headers"] == BROWSER_HEADERS
    assert options["default_headers"] is False
    assert options["impersonate"] == "chrome"
    assert options["timeout"] == 2.0
    assert "proxy" not in options


def test_403_raises_block_detected_without_proxy() -> None:
    """Without a provider a 403 surfaces immediately as BlockDetected 403."""
    adapter, _, _ = make_adapter([FakeResponse(status_code=403, text="denied")])
    with pytest.raises(BlockDetected) as excinfo:
        adapter.fetch_html(REDACTED)
    assert excinfo.value.signature == "403"
    assert "[BLOCK_DETECTED]" in str(excinfo.value)


def test_proxy_rotate_and_recover_markers(caplog) -> None:
    """Direct-first requests rotate and recover through the provider proxy."""
    proxy = StubProxy(["http://proxy.invalid:8080"])
    adapter, session, _ = make_adapter(
        [FakeResponse(status_code=403, text="denied"), FakeResponse(text="recovered")],
        proxy_provider=proxy,
    )
    assert adapter.fetch_html(REDACTED) == "recovered"
    assert len(session.calls) == 2
    assert "proxy" not in session.calls[0][1]
    assert session.calls[1][1]["proxy"] == "http://proxy.invalid:8080"
    assert proxy.proxies == []
    assert any("[PROXY_ROTATE]" in record.getMessage() for record in caplog.records)
    assert any("[PROXY_RECOVERED]" in record.getMessage() for record in caplog.records)


def test_proxy_exhausted_raises_block_detected() -> None:
    """When the provider runs out of proxies, the block is surfaced."""
    proxy = StubProxy(["http://proxy.invalid:8080"])
    adapter, _, _ = make_adapter(
        [FakeResponse(status_code=403, text="denied"), FakeResponse(status_code=403)],
        proxy_provider=proxy,
    )
    with pytest.raises(BlockDetected):
        adapter.fetch_html(REDACTED)


def test_safari_ios_profile_reaches_curl_session() -> None:
    """A persisted Safari iOS profile is passed to curl-cffi unchanged."""
    handoff = CookieHandoff.from_browser(
        proxy_url=None,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        headers={"User-Agent": "captured-agent"},
        cookies=[{"name": "reese84", "value": "clearance-value"}],
        impersonate="safari_ios",
    )
    session = FakeSession([FakeResponse(text="detail")])
    adapter = HttpDomAdapter(Budgets(), handoff=handoff, client=session)

    adapter.fetch_html(REDACTED)

    assert session.calls[0][1]["impersonate"] == "safari_ios"



def test_redaction_in_marker_disallows_credentials_and_query() -> None:
    """Block markers never leak credentials or query strings."""
    adapter, _, _ = make_adapter([FakeResponse(status_code=403)])
    with pytest.raises(BlockDetected) as excinfo:
        adapter.fetch_html(FULL_URL)
    assert "secret" not in str(excinfo.value)
    assert "PAGENO" not in str(excinfo.value)


def test_challenge_200_raises_block_detected_without_proxy(caplog) -> None:
    """A 200 puzzle page is a captcha block without a proxy provider."""
    adapter, _, _ = make_adapter([FakeResponse(text=PUZZLE_BODY)])
    with pytest.raises(BlockDetected) as excinfo:
        adapter.fetch_html(REDACTED)
    assert excinfo.value.signature == "captcha"
    assert any("[ATHOME_CHALLENGE]" in record.getMessage() for record in caplog.records)


def test_handoff_passes_exact_identity_and_cookies() -> None:
    """A handoff passes its browser identity together on every curl request."""
    handoff = make_handoff()
    session = FakeSession([FakeResponse(text="detail")])
    adapter = HttpDomAdapter(Budgets(http_timeout_s=2.0), handoff=handoff, client=session)
    assert adapter.fetch_html(REDACTED) == "detail"
    options = session.calls[0][1]
    assert options["headers"]["User-Agent"] == handoff.user_agent
    assert options["cookies"] == {"reese84": "clearance-value"}
    assert options["proxy"] == handoff.proxy_url
    assert options["impersonate"] == "chrome"
    assert options["timeout"] == 2.0


def test_handoff_block_does_not_rotate_proxy(caplog) -> None:
    """An expired handoff raises refarm signal without using another proxy."""
    handoff = make_handoff()
    provider = StubProxy(["http://different.example:8080"])
    session = FakeSession([FakeResponse(status_code=403, text="denied")])
    adapter = HttpDomAdapter(
        Budgets(), handoff=handoff, proxy_provider=provider, client=session
    )
    with pytest.raises(BlockDetected) as excinfo:
        adapter.fetch_html(REDACTED)
    assert excinfo.value.signature == "403"
    assert provider.reported == []
    assert any("[CURL_BLOCK_REHANDOFF]" in record.getMessage() for record in caplog.records)
    assert handoff.proxy_url not in str(excinfo.value)
    assert "clearance-value" not in caplog.text


def test_transient_error_retries_with_backoff() -> None:
    """A curl transport error retries with recorded exponential backoff."""
    adapter, session, sleeps = make_adapter(
        [
            curl_requests.errors.RequestsError("boom"),
            curl_requests.errors.RequestsError("boom"),
            FakeResponse(text="ok"),
        ]
    )
    assert adapter.fetch_html(REDACTED) == "ok"
    assert len(session.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_transient_error_gives_up_after_max() -> None:
    """Persistent curl transport errors exhaust retries and propagate."""
    error = curl_requests.errors.RequestsError("x")
    adapter, _, sleeps = make_adapter([error, error, error])
    with pytest.raises(curl_requests.errors.RequestsError):
        adapter.fetch_html(REDACTED)
    assert sleeps == [0.5, 1.0]


def test_fetch_dom_parses_successful_html() -> None:
    """The DOM helper parses a successful curl response."""
    adapter, _, _ = make_adapter([FakeResponse(text="<html><body><h1>Title</h1></body></html>")])
    assert adapter.fetch_dom(REDACTED).css_first("h1").text() == "Title"


def test_close_closes_injected_session() -> None:
    """The adapter delegates close to its curl session."""
    adapter, session, _ = make_adapter([FakeResponse()])
    adapter.close()
    assert session.closed
