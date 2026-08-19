"""
RUN: PYTHONPATH=src python scripts/http_manual_probe.py
"""
from __future__ import annotations

import argparse
import asyncio

import logging
import os
from pathlib import Path
import re

from athome_harness.scraping.session_refarmer import SessionRefarmer
from athome_harness.scraping.session_state import SessionState
from athome_harness.config import Budgets
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher
from athome_harness.scraping.base import BlockDetected

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"
DEFAULT_URL = "https://www.athome.co.jp/chintai/1101570928"
_DETAIL_LINK = re.compile(r'href=["\']([^"\']*/chintai/[^"\']+)["\']', re.IGNORECASE)
# AtHome's broad search regularly answers in ~5s; 15s keeps the live tests
# bounded while tolerating that latency (production default is 30s).
LIVE_TIMEOUT_S = 20.0
# Set to true to capture and save screenshot, video and tracestack
DEBUG = False

def _parser() -> argparse.ArgumentParser:
    """Build the manual probe command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--debug-dir", type=Path, default=Path("debug"))
    parser.add_argument("--proxy", default=None, help="Optional Patchright proxy server URL")
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    parser.add_argument(
        "--solve-mode",
        choices=["none", "click", "capsolver"],
        default="click",
        help=(
            "Challenge handling strategy: 'none' (observe only), 'click' "
            "(auto-interact), 'capsolver' (API solver)"
        ),
    )
    parser.add_argument("--capsolver-key", default=os.getenv("CAPSOLVER_API_KEY", None))
    return parser

async def _run(args: argparse.Namespace) -> None:
    """ Class """
    budgets = Budgets(http_timeout_s=LIVE_TIMEOUT_S)
    debug_dir = args.debug_dir
    
    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
            return HttpDomAdapter(budgets, handoff=handoff, debug=True)

    http_adapter = build_adapter(handoff=None)

    try:
        html = http_adapter.fetch_html(DEFAULT_URL)
        (debug_dir / f"http_first.html").write_text(html, encoding="utf-8")
    except BlockDetected as first_block:
        logger.warning(
            "[REHANDOFF_TRIGGERED] url=<%s> signature=<%s>",
            DEFAULT_URL,
            first_block.signature
        )
        handoff: CookieHandoff = await PlaywrightCookieFetcher(
                        url=DEFAULT_URL,
                        debug_dir=debug_dir,
                    ).farm()
        rebound = build_adapter(handoff)
        try:
            html = rebound.fetch_html(DEFAULT_URL)
            (debug_dir / f"http_second.html").write_text(html, encoding="utf-8")
        except BlockDetected as block:
            logger.warning(
                "[REHANDOFF_STILL_BLOCKED] url=<%s> signature=<%s>",
                DEFAULT_URL,
                block.signature,
            )
        
    # For debug purpose    
    if http_adapter._rawResponse is not None:
        (debug_dir / f"http_first_raw.html").write_text(http_adapter._rawResponse.text, encoding="utf-8")
    if rebound._rawResponse is not None:
        (debug_dir / f"http_second_raw.html").write_text(rebound._rawResponse.text, encoding="utf-8")

    http_adapter.close()
    rebound.close()

def main() -> None:
    """Parse arguments and run the headed manual probe."""
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()