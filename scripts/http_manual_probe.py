"""
Manual end-to-end probe for the HttpDom -> block -> PlaywrightCookie ->
handoff -> HttpDom fallback pipeline.

It mirrors ``scripts/playwright_manual_probe.py`` but drives the curl-cffi
side, exactly as production uses ``SessionRefarmer``. On a direct (unbound)
fetch that hits an AtHome block, it farms a browser ``CookieHandoff`` and
rebinds a fresh adapter with that session so you can inspect whether reusing
the farmed session clears the block.

Debug mode records the raw curl-cffi responses (first and rebound) so you can
diff the blocked versus rebound HTML bodies.

RUN: PYTHONPATH=src python scripts/http_manual_probe.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from athome_harness.config import Budgets
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.http_adapter import HttpDomAdapter
from athome_harness.scraping.playwright_cookie_fetcher import PlaywrightCookieFetcher

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"
LIVE_TIMEOUT_S = 20.0


def _parser() -> argparse.ArgumentParser:
    """Build the manual probe command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--debug-dir", type=Path, default=Path("debug"))
    parser.add_argument(
        "--capsolver-key",
        default=os.getenv("CAPSOLVER_API_KEY", None),
        help="CapSolver API key for the browser challenge solver.",
    )
    return parser


def _save_response(debug_dir: Path, name: str, adapter: HttpDomAdapter) -> None:
    """Persist the last raw curl-cffi response body when the adapter captured it."""
    raw = adapter.raw_response
    if raw is not None:
        (debug_dir / name).write_text(raw.text, encoding="utf-8")


async def _build_adapter_and_run(args: argparse.Namespace) -> None:
    """Run the full handoff fallback loop and dump debug artifacts per stage."""
    budgets = Budgets(http_timeout_s=LIVE_TIMEOUT_S)
    debug_dir = args.debug_dir
    debug_dir.mkdir(parents=True, exist_ok=True)

    def build_adapter(handoff: CookieHandoff | None) -> HttpDomAdapter:
        return HttpDomAdapter(budgets, handoff=handoff, debug=True)

    first = build_adapter(handoff=None)
    rebound: HttpDomAdapter | None = None

    try:
        try:
            html = first.fetch_html(args.url)
            (debug_dir / "http_first.html").write_text(html, encoding="utf-8")
        except BlockDetected as first_block:
            logger.warning(
                "[REHANDOFF_TRIGGERED] url=<%s> signature=<%s>",
                args.url,
                first_block.signature,
            )
            handoff: CookieHandoff = await PlaywrightCookieFetcher(
                url=args.url,
                debug_dir=debug_dir,
                capsolver_key=args.capsolver_key,
            ).farm()
            rebound = build_adapter(handoff)
            try:
                html = rebound.fetch_html(args.url)
                (debug_dir / "http_second.html").write_text(html, encoding="utf-8")
            except BlockDetected as block:
                logger.warning(
                    "[REHANDOFF_STILL_BLOCKED] url=<%s> signature=<%s>",
                    args.url,
                    block.signature,
                )
    finally:
        _save_response(debug_dir, "http_first_raw.html", first)
        if rebound is not None:
            _save_response(debug_dir, "http_second_raw.html", rebound)
        first.close()
        if rebound is not None:
            rebound.close()


def main() -> int:
    """Parse arguments and run the headed manual probe."""
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_build_adapter_and_run(_parser().parse_args()))
    except (KeyboardInterrupt, SystemExit):
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
