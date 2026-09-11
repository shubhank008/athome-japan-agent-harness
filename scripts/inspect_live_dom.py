"""Inspect live AtHome list markup against the parser's selector contract.

The command fetches one live list page through the production HTTP adapter,
rejects challenge pages, and prints counts for legacy and current selectors.
It never rewrites source selectors or saves challenge HTML. Use it as a
human-reviewed maintenance check when AtHome markup changes.

RUN: set -a; . ./.env; set +a; PYTHONPATH=src python scripts/inspect_live_dom.py
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from selectolax.parser import HTMLParser

from athome_harness.config import Budgets
from athome_harness.providers import build_production_fetch, load_settings
from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge

logger = logging.getLogger(__name__)
DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"

SELECTORS = {
    "legacy buildings": "div.p-property--building",
    "current property cards": "div.property-card",
    "current room sections": "div.property-card div.room-info-section",
    "current room links": "div.property-card div.room-info-section a[href*='/chintai/']",
    "legacy room boxes": "div.p-property__room--detailbox",
    "legacy detail keys": "div.p-property__room--detailbox[data-bukken-no]",
}


def _parser() -> argparse.ArgumentParser:
    """Build the inspection command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--save-html", type=Path, default=None)
    return parser


def _inspect(html: str) -> dict[str, int]:
    """Count known DOM shapes in validated live HTML."""
    challenge = detect_athome_challenge(html)
    if challenge is not None:
        raise RuntimeError(f"AtHome challenge detected ({challenge}); refusing to inspect")
    tree = HTMLParser(html)
    return {name: len(tree.css(selector)) for name, selector in SELECTORS.items()}


def main() -> int:
    """Fetch and print selector counts for one live list page."""
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
    )
    settings = load_settings()
    fetch = build_production_fetch(Budgets(http_timeout_s=settings.http_timeout_s), settings)
    try:
        html = fetch(args.url)
        counts = _inspect(html)
        print(f"url: {redact_url(args.url)}")
        print(f"html_chars: {len(html)}")
        for name, count in counts.items():
            print(f"{name}: {count}")
        if args.save_html is not None:
            args.save_html.parent.mkdir(parents=True, exist_ok=True)
            args.save_html.write_text(html, encoding="utf-8")
            print(f"saved_html: {args.save_html.resolve()}")
        return 0
    finally:
        close = getattr(fetch, "close", None)
        if close is not None:
            close()


if __name__ == "__main__":
    raise SystemExit(main())
