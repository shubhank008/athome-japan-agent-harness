"""Tests for the Patchright-to-curl-cffi cookie handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.playwright_cookie_fetcher import (
    PlaywrightCookieFetcher,
    PlaywrightCookieFetcherError,
)
from athome_harness.scraping.session_state import SessionState

GOOD_HTML = "<html><body>" + ("AtHome listing content " * 20) + "</body></html>"
PUZZLE_HTML = "<html><body><h1>Click to verify</h1></body></html>"


class FakeRequest:
    """Navigation request exposing the headers used by the browser."""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": "Fake Browser/1.0",
            "Accept-Language": "ja-JP",
        }

    def is_navigation_request(self) -> bool:
        """Identify this request as the main page navigation."""
        return True


class FakeLocator:
    """Visible verification control used by the challenge-flow test."""

    def __init__(self, page: FakePage, *, kind: str = "text") -> None:
        self.page = page
        self.kind = kind

    @property
    def first(self) -> FakeLocator:
        """Match Patchright's first locator property."""
        return self

    async def count(self) -> int:
        """Return one matching control."""
        return 1

    async def is_visible(self) -> bool:
        """Report that the verification control is visible."""
        return True

    async def wait_for(self, *, state: str, timeout: int) -> None:
        """Accept the visibility wait used by the real click path."""
        assert state == "visible"
        assert timeout == 5000

    async def bounding_box(self) -> None:
        """Force the deterministic fallback click path."""
        return None

    async def hover(self) -> None:
        """Accept the humanized hover step."""
        return None

    async def click(self, *, delay: int) -> None:
        """Advance the page from challenge to rendered content."""
        self.page.clicks += 1
        self.page.clicked_kinds.append(self.kind)
        if self.page.advance_on_click:
            self.page.current_html = GOOD_HTML


class EmptyLocator:
    """Locator substitute representing no semantic control match."""

    @property
    def first(self) -> EmptyLocator:
        """Match Patchright's first locator property."""
        return self

    async def count(self) -> int:
        """Report no matching controls."""
        return 0


class FakeFrame:
    """Child frame exposing a semantic verification button."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.url = "https://www.athome.co.jp/security-frame"

    def get_by_role(self, role: str, *, name: Any) -> FakeLocator | EmptyLocator:
        """Return a semantic button only in the child frame."""
        assert role in {"button", "link"}
        assert name.search("Click to verify")
        return FakeLocator(self.page, kind="button") if role == "button" else EmptyLocator()

    def get_by_text(self, pattern: Any) -> EmptyLocator:
        """Avoid selecting the less-preferred text fallback in this frame."""
        assert pattern.search("Click to verify")
        return EmptyLocator()


class FakePage:
    """Small Patchright page substitute exercising the real farmer logic."""

    def __init__(
        self,
        initial_html: str,
        *,
        advance_on_click: bool = True,
        child_role: bool = False,
    ) -> None:
        self.current_html = initial_html
        self.advance_on_click = advance_on_click
        self.child_role = child_role
        self.clicks = 0
        self.clicked_kinds: list[str] = []
        self.url = "https://www.athome.co.jp/chintai/osaka/list/"
        self.video = None
        self._request_handler: Any = None
        self._child_frame = FakeFrame(self) if child_role else None

    @property
    def frames(self) -> list[object]:
        """Expose the fake main frame and optional stable child frame."""
        if self._child_frame is not None:
            return [self, self._child_frame]
        return [self]

    async def goto(self, url: str, *, wait_until: str) -> None:
        """Emit the main navigation request before page rendering."""
        assert url.startswith("https://www.athome.co.jp")
        assert wait_until == "domcontentloaded"
        if self._request_handler is not None:
            self._request_handler(FakeRequest())

    async def content(self) -> str:
        """Return the current page HTML."""
        return self.current_html

    async def evaluate(self, expression: str) -> str:
        """Return the browser's exact user agent."""
        assert expression == "navigator.userAgent"
        return "Fake Browser/1.0"

    def on(self, event: str, handler: Any) -> None:
        """Register the navigation request listener."""
        assert event == "request"
        self._request_handler = handler

    def get_by_role(self, role: str, *, name: Any) -> FakeLocator | EmptyLocator:
        """Return no semantic control so the text fallback is exercised."""
        assert role in {"button", "link"}
        assert name.search("Click to verify")
        return EmptyLocator()

    def get_by_text(self, pattern: Any) -> FakeLocator | EmptyLocator:
        """Return the fake control unless a child semantic role is configured."""
        assert pattern.search("Click to verify")
        return EmptyLocator() if self.child_role else FakeLocator(self, kind="text")

    async def screenshot(self, *, path: str) -> None:
        """Write a deterministic screenshot placeholder."""
        Path(path).write_bytes(b"fake-png")


class FakeContext:
    """Browser context with deterministic cookies and one page."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False
        self.raise_on_close = False

    async def new_page(self) -> FakePage:
        """Return the configured page."""
        return self.page

    async def cookies(self) -> list[dict[str, object]]:
        """Return the clearance cookies harvested by the browser."""
        return [{"name": "reese84", "value": "clearance", "domain": ".athome.co.jp"}]

    async def close(self) -> None:
        """Record context shutdown."""
        self.closed = True
        if self.raise_on_close:
            raise RuntimeError("context close failed")


class FakeChromium:
    """Persistent Chrome launcher capturing Patchright context options."""

    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.user_data_dir: str | None = None
        self.options: dict[str, object] | None = None

    async def launch_persistent_context(
        self,
        *,
        user_data_dir: str,
        **options: object,
    ) -> FakeContext:
        """Return the fake context and retain persistent Chrome settings."""
        self.user_data_dir = user_data_dir
        self.options = options
        assert options["channel"] == "chrome"
        assert options["headless"] is True
        assert options["no_viewport"] is True
        assert options["locale"] == "ja-JP"
        assert options["proxy"] is None or options["proxy"]
        # The lean production farmer never requests video recording.
        assert "record_video_dir" not in options
        return self.context


class FakePlaywright:
    """Async Patchright context manager used by the tests."""

    def __init__(self, context: FakeContext) -> None:
        self.chromium = FakeChromium(context)

    async def __aenter__(self) -> FakePlaywright:
        """Enter the fake Patchright runtime."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Exit the fake Patchright runtime."""
        return None


@pytest.fixture
def patch_playwright(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch Patchright startup while retaining real farmer behavior."""
    state: dict[str, object] = {}

    def install(page: FakePage) -> None:
        context = FakeContext(page)
        runtime = FakePlaywright(context)
        monkeypatch.setattr(
            "athome_harness.scraping.playwright_cookie_fetcher.async_playwright",
            lambda: runtime,
        )
        state.update(context=context, chromium=runtime.chromium)
        monkeypatch.setattr(
            "athome_harness.scraping.playwright_cookie_fetcher._legacy_stealth_async",
            lambda _: _completed(),
        )

    state["install"] = install
    return state


async def _completed() -> None:
    """Provide an awaitable no-op for the stealth hook."""
    return None


def context_video_was_requested(patch_playwright: dict[str, object]) -> bool:
    """Return whether the fake persistent context was asked to record video."""
    chromium = cast(FakeChromium, patch_playwright["chromium"])
    options = chromium.options or {}
    return "record_video_dir" in options


@pytest.mark.asyncio
async def test_farm_persists_handoff_for_curl_workers(
    tmp_path: Path,
    patch_playwright: dict[str, object],
) -> None:
    """A rendered page produces matching proxy, UA, headers, and cookies."""
    page = FakePage(GOOD_HTML)
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    handoff_path = tmp_path / "handoff.json"
    fetcher = PlaywrightCookieFetcher(
        proxy_url="http://proxy.example:8080",
        debug_dir=tmp_path,
        handoff_path=handoff_path,
        wait_seconds=0,
    )

    handoff = await fetcher.farm()

    assert handoff.proxy_identity == "proxy_example_8080"
    assert handoff.cookie_values == {"reese84": "clearance"}
    assert handoff.to_curl_cffi_kwargs()["proxy"] == "http://proxy.example:8080"
    assert handoff.to_curl_cffi_kwargs()["headers"] == {
        "User-Agent": "Fake Browser/1.0",
        "Accept-Language": "ja-JP",
    }
    assert handoff_path.exists()
    reloaded = CookieHandoff.load(handoff_path)
    assert reloaded.to_curl_cffi_kwargs() == handoff.to_curl_cffi_kwargs()
    assert (tmp_path / "cookies.txt").read_text() == "reese84=clearance\n"

    session_state = SessionState.load(tmp_path / "session_state.json")
    assert session_state.cookies == {"reese84": "clearance"}
    assert session_state.user_agent == "Fake Browser/1.0"
    assert session_state.proxy_url == "http://proxy.example:8080"

    context = cast(FakeContext, patch_playwright["context"])
    assert context.closed
    chromium = cast(FakeChromium, patch_playwright["chromium"])
    assert chromium.user_data_dir


@pytest.mark.asyncio
async def test_lean_farm_captures_no_diagnostics_but_saves_session_state(
    tmp_path: Path,
    patch_playwright: dict[str, object],
) -> None:
    """The lean production farmer records no trace/video but persists the handoff."""
    page = FakePage(GOOD_HTML)
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    fetcher = PlaywrightCookieFetcher(debug_dir=tmp_path, wait_seconds=0)

    await fetcher.farm()

    assert (tmp_path / "session_state.json").exists()
    assert (tmp_path / "cookie_handoff_direct.json").exists()
    assert not (tmp_path / "playwright_challenge_trace.zip").exists()
    assert not (tmp_path / "playwright_events.jsonl").exists()
    assert context_video_was_requested(patch_playwright) is False


@pytest.mark.asyncio
async def test_farm_clicks_basic_challenge_and_persists_handoff(
    tmp_path: Path,
    patch_playwright: dict[str, object],
) -> None:
    """A visible Click-to-Verify challenge is clicked and the session persisted."""
    page = FakePage(PUZZLE_HTML)
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    fetcher = PlaywrightCookieFetcher(
        debug_dir=tmp_path,
        wait_seconds=0,
        min_html_length=20,
    )

    handoff = await fetcher.farm()

    assert page.clicks == 1
    assert handoff.cookie_values == {"reese84": "clearance"}
    assert (tmp_path / "cookie_handoff_direct.json").exists()
    assert (tmp_path / "session_state.json").exists()
    # The lean production farmer captures no diagnostic artifacts.
    assert not (tmp_path / "playwright_before.html").exists()
    assert not (tmp_path / "playwright_after.png").exists()
    assert not (tmp_path / "playwright_events.jsonl").exists()
    assert not (tmp_path / "playwright_challenge_trace.zip").exists()


@pytest.mark.asyncio
async def test_farm_prefers_semantic_control_in_child_frame(
    tmp_path: Path,
    patch_playwright: dict[str, object],
) -> None:
    """A semantic button in a child frame is selected before text fallback."""
    page = FakePage(PUZZLE_HTML, child_role=True)
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    fetcher = PlaywrightCookieFetcher(debug_dir=tmp_path, wait_seconds=0)

    handoff = await fetcher.farm()

    assert page.clicks == 1
    assert handoff.cookie_values == {"reese84": "clearance"}
    # The semantic child-frame button is preferred over the main-frame text.
    assert page.clicked_kinds == ["button"]


@pytest.mark.asyncio
async def test_farm_rejects_challenge_that_remains_after_click(
    tmp_path: Path,
    patch_playwright: dict[str, object],
) -> None:
    """A challenge that persists is rejected instead of handed to workers."""
    page = FakePage(PUZZLE_HTML, advance_on_click=False)
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    fetcher = PlaywrightCookieFetcher(debug_dir=tmp_path, wait_seconds=0)

    with pytest.raises(PlaywrightCookieFetcherError, match="reason=<challenge>"):
        await fetcher.farm()

    assert not (tmp_path / "cookie_handoff_direct.json").exists()
    # A rejected challenge persists no diagnostic artifacts in the lean farmer.
    assert not (tmp_path / "playwright_after.html").exists()
    assert not (tmp_path / "playwright_challenge_trace.zip").exists()


@pytest.mark.asyncio
async def test_cleanup_failures_do_not_mask_render_error(
    tmp_path: Path,
    patch_playwright: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A context close error is logged without replacing the render cause."""
    page = FakePage("<html></html>")
    install = patch_playwright["install"]
    assert callable(install)
    install(page)
    context = cast(FakeContext, patch_playwright["context"])
    context.raise_on_close = True
    fetcher = PlaywrightCookieFetcher(debug_dir=tmp_path, wait_seconds=0)

    with (
        caplog.at_level("ERROR"),
        pytest.raises(PlaywrightCookieFetcherError, match="reason=<render>"),
    ):
        await fetcher.farm()

    assert context.closed
    assert "[PATCHRIGHT_CONTEXT_CLOSE_FAILED]" in caplog.text
