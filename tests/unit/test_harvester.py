"""Unit tests for the pagination engine (M6, T24).

Exercises the :class:`Harvester` with a real fixture page and a fake fetch
callable (small fakes only at the transport boundary). Proves budget
enforcement (pages and runtime), natural end-of-data, abort-on-block, and
fail-closed challenge handling, and asserts the exact marker strings the
marker contract requires.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.harvester import Harvester
from athome_harness.scraping.list_parser import parse_list_page

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
LIST_HTML = (FIXTURES / "osaka_rental_list.html").read_text(encoding="utf-8")

# A prompt list page that carries no listings (natural end of data).
EMPTY_LIST_HTML = "<html><body><div class='p-property--building'></div></body></html>"

# A challenge page that must fail closed (never parsed as content).
_CHALLENGE_HTML = (
    "<html><body>Please <b>click to verify</b> you are human. "
    "To regain access, please make sure that cookies and JavaScript are enabled.</body></html>"
)


class _FakeFetch:
    """Serves HTML by page number; optional block and challenge injection."""

    def __init__(
        self,
        *,
        pages: dict[int, str],
        block_on: set[int] | None = None,
        challenge_on: set[int] | None = None,
    ) -> None:
        self._pages = pages
        self._block_on = block_on or set()
        self._challenge_on = challenge_on or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        page = int(url.split("page=", 1)[1])
        if page in self._block_on:
            raise BlockDetected(url, "403")
        if page in self._challenge_on:
            return _CHALLENGE_HTML
        return self._pages.get(page, EMPTY_LIST_HTML)


class _CountingClock:
    """Auto-advancing monotonic clock for deterministic runtime-budget tests."""

    def __init__(self, step: float = 1.0) -> None:
        self._value = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self._value
        self._value += self._step
        return value


def _build_page_url(page: int) -> str:
    return f"https://example.invalid/athome/list?page={page}"


def _make_harvester(
    fetch: _FakeFetch,
    budgets: Budgets | None = None,
    clock=None,
) -> Harvester:
    return Harvester(
        fetch_page=fetch,
        parse_page=parse_list_page,
        build_page_url=_build_page_url,
        budgets=budgets or Budgets(),
        clock=clock,
    )


def test_natural_end_on_empty_page(caplog: pytest.LogCaptureFixture) -> None:
    """The loop stops cleanly when a page returns no listings (partial=False)."""
    fetch = _FakeFetch(pages={1: LIST_HTML, 2: EMPTY_LIST_HTML})
    caplog.set_level(logging.INFO)
    caplog.clear()
    result = _make_harvester(fetch).harvest()

    assert result.partial is False
    assert result.abort_reason is None
    assert result.pages_scraped == 2
    assert len(result.listings) == 460
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[HARVEST_START] expected_pages=0 max_pages=100") for m in messages)
    assert any(m.startswith("[HARVEST_DONE] pages=2 listings=460 partial=false") for m in messages)


def test_max_pages_budget_triggers_partial(caplog: pytest.LogCaptureFixture) -> None:
    """Hitting the page cap aborts with partial=True, reason=pages."""
    budgets = Budgets(max_pages=2)
    fetch = _FakeFetch(pages={1: LIST_HTML, 2: LIST_HTML})
    caplog.clear()
    result = _make_harvester(fetch, budgets).harvest()

    assert result.partial is True
    assert result.abort_reason == "pages"
    assert result.pages_scraped == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BUDGET_HIT] kind=pages limit=2") for m in messages)
    assert any(m.startswith("[PARTIAL_REPORT] reason=budget listings=") for m in messages)


def test_runtime_budget_triggers_partial(caplog: pytest.LogCaptureFixture) -> None:
    """A zero runtime budget aborts before any page fetch (reason=runtime)."""
    budgets = Budgets(runtime_minutes=0)
    fetch = _FakeFetch(pages={1: LIST_HTML})
    caplog.clear()
    result = _make_harvester(fetch, budgets, clock=_CountingClock()).harvest()

    assert result.partial is True
    assert result.abort_reason == "runtime"
    assert result.pages_scraped == 0
    assert fetch.calls == []
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BUDGET_HIT] kind=runtime limit=0") for m in messages)


def test_block_aborts_with_partial(caplog: pytest.LogCaptureFixture) -> None:
    """A 403 block on a later page aborts with partial=True, reason=block."""
    fetch = _FakeFetch(pages={1: LIST_HTML, 2: LIST_HTML}, block_on={2})
    caplog.clear()
    result = _make_harvester(fetch).harvest()

    assert result.partial is True
    assert result.abort_reason == "block"
    assert result.pages_scraped == 1
    assert len(result.listings) == 460
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        m.startswith("[BLOCK_DETECTED] url=<https://example.invalid/athome/list> signature=<403>")
        for m in messages
    )
    assert any(m.startswith("[PARTIAL_REPORT] reason=block listings=460") for m in messages)


def test_challenge_page_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    """A challenge page is never parsed as content; the harvest fails closed."""
    fetch = _FakeFetch(pages={1: LIST_HTML}, challenge_on={1})
    caplog.clear()
    result = _make_harvester(fetch).harvest()

    assert result.partial is True
    assert result.abort_reason == "block"
    assert result.pages_scraped == 0
    assert result.listings == []
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[ATHOME_CHALLENGE] url=<") and "kind=<puzzle>" in m for m in messages)


def test_deduplicates_across_pages() -> None:
    """The same listing served on two pages is stored once by internal_id."""
    fetch = _FakeFetch(pages={1: LIST_HTML, 2: LIST_HTML})
    result = _make_harvester(fetch).harvest()
    ids = [listing.internal_id for listing in result.listings]
    assert len(ids) == len(set(ids)) == 460
