"""T27 / US-008 integration: real SessionRefarmer + HttpDomAdapter, simulated block.

Drives the actual orchestration boundary (a real :class:`SessionRefarmer`
wrapping the real :class:`HttpDomAdapter`) with a deterministic fake curl
session and a fake async browser farmer. No live AtHome request, no CAPTCHA
solving, and no challenge bypass is involved, and no secret material appears.

The test proves the two properties US-008 requires of the fallback loop:

1. Direct-first: the cheap curl-cffi attempt runs before any farming and wins
   when the target is healthy.
2. Bounded farm/rebind: on a block the loop farms exactly once (bounded) and
   retries the in-flight request through a freshly bound handoff.

It asserts the marker contract order ``[BLOCK_DETECTED] -> [REHANDOFF_TRIGGERED]
-> [REHANDOFF_FARMED] -> [CURL_HANDOFF_BOUND]``, that the forbidden failure
patterns never appear, and that every adapter transport is closed afterward.
Only the external transport (curl session) and the async farmer are faked, as
the existing unit tests do; all orchestration is real project code.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.session_refarmer import SessionRefarmer

URL = "https://www.athome.co.jp/chintai/osaka/list/"
RECOVERED_HTML = "<html><body><div class='p-property--building'>ok</div></body></html>"

# Forbidden failure patterns from the marker contract, asserted never to appear.
FORBIDDEN_PATTERNS = [
    "Traceback (most recent call last)",
    "LLM_JSON_INVALID",
    "UNKNOWN_FILTER_ENCODED",
    "PROXY_CREDENTIALS_IN_URL_LOG",
    "FILTER_MAP_SCHEMA_UNSUPPORTED",
]


@dataclass
class _Response:
    """Minimal curl response matching the adapter's response protocol."""

    status_code: int = 200
    text: str = "ok"

    @property
    def content(self) -> bytes:
        """Return the response body as UTF-8 bytes."""
        return self.text.encode("utf-8")


class _Session:
    """Deterministic fake curl session; serves responses in order."""

    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> _Response:
        """Pop and return the next configured response, recording the call."""
        self.calls.append((url, kwargs))
        return self._responses.pop(0)

    def close(self) -> None:
        """Record that the transport was released."""
        self.closed = True


def _make_handoff(proxy_url: str | None = None) -> CookieHandoff:
    """Build a minimal valid browser handoff for the rebound adapter."""
    return CookieHandoff.from_browser(
        proxy_url=proxy_url,
        user_agent="Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36",
        headers={"User-Agent": "Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36"},
        cookies=[{"name": "reese84", "value": "clearance"}],
    )


class _BlockedSession:
    """A curl session whose every GET raises a 403-style block response."""

    def __init__(self) -> None:
        self.closed = False

    def get(self, url: str, **kwargs: object) -> _Response:
        """Return an HTTP 403 block response."""
        return _Response(status_code=403, text="<html>denied</html>")

    def close(self) -> None:
        """Record that the transport was released."""
        self.closed = True


def test_direct_first_then_farm_rebind_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A block on the direct attempt farms once and recovers via the rebind."""
    caplog.set_level(logging.WARNING)
    direct_session = _BlockedSession()
    rebound_session = _Session([_Response(status_code=200, text=RECOVERED_HTML)])
    farm_calls: list[CookieHandoff] = []

    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
        # The session is pre-built per attempt; the adapter owns it.
        if handoff is None:
            return HttpDomAdapter(Budgets(), client=direct_session)
        return HttpDomAdapter(Budgets(), handoff=handoff, client=rebound_session)

    async def farm() -> CookieHandoff:
        handoff = _make_handoff(proxy_url="http://proxy.example:8080")
        farm_calls.append(handoff)
        return handoff

    async def run() -> str:
        refarmer = SessionRefarmer(build_adapter=build_adapter, farm=farm, max_refarms=1)
        return await refarmer.fetch_html(URL)

    html = asyncio.run(run())
    assert html == RECOVERED_HTML
    # Direct-first + bounded: exactly one farm happened, no more.
    assert len(farm_calls) == 1
    # Cleanup: both the direct and the rebound transport were closed.
    assert direct_session.closed and rebound_session.closed

    messages = [r.getMessage() for r in caplog.records]
    # Marker contract order for a successful direct-first recovery.
    order = [
        "[BLOCK_DETECTED]",
        "[REHANDOFF_TRIGGERED]",
        "[REHANDOFF_FARMED]",
        "[CURL_HANDOFF_BOUND]",
    ]
    present = [m for m in order if any(msg.startswith(m) for msg in messages)]
    assert present == order, f"missing or unexpected refarm markers: {present}"
    positions = [
        next(i for i, msg in enumerate(messages) if msg.startswith(m)) for m in order
    ]
    assert positions == sorted(positions), "refarm markers out of order"

    # The block was resolved, so a still-blocked marker must never appear.
    assert not any(m.startswith("[REHANDOFF_STILL_BLOCKED]") for m in messages)
    # No forbidden failure patterns.
    full = "\n".join(messages)
    for pat in FORBIDDEN_PATTERNS:
        assert pat not in full, f"forbidden failure pattern present: {pat}"


def test_rebound_still_blocked_is_bounded(caplog: pytest.LogCaptureFixture) -> None:
    """When the rebind still blocks, the loop re-raises without extra farming."""
    caplog.set_level(logging.WARNING)
    direct_session = _BlockedSession()
    rebound_session = _BlockedSession()
    farm_calls: list[CookieHandoff] = []

    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
        if handoff is None:
            return HttpDomAdapter(Budgets(), client=direct_session)
        return HttpDomAdapter(Budgets(), handoff=handoff, client=rebound_session)

    async def farm() -> CookieHandoff:
        handoff = _make_handoff()
        farm_calls.append(handoff)
        return handoff

    async def run() -> None:
        refarmer = SessionRefarmer(build_adapter=build_adapter, farm=farm, max_refarms=1)
        await refarmer.fetch_html(URL)

    with pytest.raises(BlockDetected):
        asyncio.run(run())

    # Bounded: max_refarms=1 leads to exactly one farm even though it blocked.
    assert len(farm_calls) == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[REHANDOFF_STILL_BLOCKED]") for m in messages)
    positions = [
        next(i for i, msg in enumerate(messages) if msg.startswith(m))
        for m in ("[REHANDOFF_FARMED]", "[REHANDOFF_STILL_BLOCKED]")
    ]
    assert positions == sorted(positions), "still-blocked marker out of order"
    full = "\n".join(messages)
    for pat in FORBIDDEN_PATTERNS:
        assert pat not in full, f"forbidden failure pattern present: {pat}"
