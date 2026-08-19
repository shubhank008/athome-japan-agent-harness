"""Opt-in live Playwright browser-session farming validation.

Scope: this file exercises the Patchright layer only, exactly as it behaves
in production (shared launch options, challenge handling, handoff and
``session_state.json`` persistence, then one curl-cffi replay of that
handoff to prove the session is accepted). The HttpDom-fallback
orchestration and multi-page curl workers have their own live test in
``test_session_refarm_live.py``.
RUN: `ATHOME_LIVE_TEST=1 pytest -sm live tests/live/test_playwright_curl_live.py`
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from athome_harness.config import Budgets
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.scraping.session_state import SessionState

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.live
BROAD_SEARCH_URL = "https://www.athome.co.jp/chintai/osaka/list/"
MIN_EXPECTED_HTML = 200
# AtHome's broad search regularly answers in ~5s; 15s keeps the live test
# bounded while tolerating that latency (production default is 30s).
LIVE_TIMEOUT_S = 30.0


@pytest.mark.asyncio
async def test_playwright_handoff_persists_and_replays(tmp_path: Path) -> None:
    """Farm one browser session, persist its state, and replay it once via curl."""
    if os.getenv("ATHOME_LIVE_TEST") != "1":
        pytest.skip("set ATHOME_LIVE_TEST=1 to access AtHome")

    fetcher = PlaywrightCookieFetcher(url=BROAD_SEARCH_URL, debug_dir=tmp_path)
    handoff = await fetcher.farm()

    # The shared session_state.json handoff artifact must exist and match the
    # handoff the browser just produced, so curl-cffi workers can load it later.
    session = SessionState.load(tmp_path / "session_state.json")
    assert session.cookies
    assert session.user_agent == handoff.user_agent
    assert session.to_cookie_handoff().cookie_values == handoff.cookie_values

    # One curl-cffi replay proves the farmed session is accepted by AtHome.
    adapter = HttpDomAdapter(Budgets(http_timeout_s=LIVE_TIMEOUT_S), handoff=handoff)
    try:
        broad_html = adapter.fetch_html(BROAD_SEARCH_URL)
    finally:
        adapter.close()

    assert detect_athome_challenge(broad_html) is None
    assert len(broad_html) > MIN_EXPECTED_HTML
    logger.warning(
        "[CURL_PLAYWRIGHT_INTEGRATION] cookies=<%d> chrome_major=<%s> timeout=<15>",
        len(session.cookies),
        session.chrome_major_version,
    )
