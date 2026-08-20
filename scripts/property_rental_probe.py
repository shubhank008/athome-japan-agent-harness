"""Bounded operator probe for a single property (rental) listing flow.

Drives the real ``HttpDomAdapter`` -> ``list_parser`` -> ``detail_parser`` path
for one property the way a human operator would inspect a single listing. It is
safe to run with ``--help`` and its basic verification path needs no live
request.

Two input modes:

* ``--input-mode url`` (default): fetches a single AtHome list page over the
  HTTP adapter, parses the first listing, then fetches and parses that
  listing's detail page. Each request is bounded by ``--timeout`` and the probe
  fails closed on a block or an AtHome challenge.
* ``--input-mode fixture``: parses an already-captured list HTML file (and,
  optionally, its matching detail HTML) with no network. This is the
  deterministic, offline verification path.

Every stage validates content before writing any artifact, redacts URLs in
diagnostics, and closes every adapter and transport in all paths (including
error paths). No credentials, cookies, proxy URLs, or challenge HTML are ever
written to tracked artifacts; the debug directory is ignored by git.

RUN (network):   PYTHONPATH=src python scripts/property_rental_probe.py --url <LIST_URL>
RUN (offline):   PYTHONPATH=src python scripts/property_rental_probe.py --input-mode fixture \
                     --list-html tests/fixtures/osaka_rental_list.html \
                     --detail-html tests/fixtures/detail_1122949022.html
HELP (no net):   PYTHONPATH=src python scripts/property_rental_probe.py --help
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure imports resolve whether the probe is run as `python scripts/...` or as
# a module: expose both the repo root (for ``scripts.*``) and ``src`` (for the
# ``athome_harness`` package) on the module search path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from athome_harness.config import Budgets  # noqa: E402
from athome_harness.models import ListingDetail, ListingSummary  # noqa: E402
from athome_harness.scraping.base import BlockDetected, redact_url  # noqa: E402
from athome_harness.scraping.detail_parser import parse_detail_page  # noqa: E402
from athome_harness.scraping.http_adapter import HttpDomAdapter  # noqa: E402
from athome_harness.scraping.list_parser import parse_list_page  # noqa: E402
from scripts.probe_common import (  # noqa: E402
    ProbeContentError,
    redact_diagnostics,
    safe_artifact_path,
    validate_page_content,
)

logger = logging.getLogger(__name__)

DEFAULT_LIST_URL = "https://www.athome.co.jp/chintai/osaka/list/"
DEFAULT_TIMEOUT_S = 20.0


def _parser() -> argparse.ArgumentParser:
    """Build the single-property probe command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-mode",
        choices=("url", "fixture"),
        default="url",
        help="url: fetch live; fixture: parse captured HTML offline.",
    )
    parser.add_argument("--url", default=DEFAULT_LIST_URL, help="AtHome list page to probe.")
    parser.add_argument(
        "--list-html",
        type=Path,
        default=None,
        help="Captured list HTML file to parse in fixture mode.",
    )
    parser.add_argument(
        "--detail-html",
        type=Path,
        default=None,
        help="Optional captured detail HTML file to parse in fixture mode.",
    )
    parser.add_argument(
        "--debug-dir", type=Path, default=Path("debug"), help="Directory for probe artifacts."
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Per-request timeout in seconds."
    )
    return parser


def _redacted_args(args: argparse.Namespace) -> str:
    """Return a redacted rendering of the CLI args for diagnostics."""
    return redact_diagnostics(str(vars(args)))


def _parse_summaries(html: str, *, source: str) -> list[ListingSummary]:
    """Validate and parse a list page into summaries, failing closed."""
    validate_page_content(html, stage="list_parse", source=source)
    summaries = parse_list_page(html)
    if not summaries:
        raise ProbeContentError("list_parse", source, "no listings parsed from the page")
    if not all(isinstance(s, ListingSummary) for s in summaries):
        raise ProbeContentError("list_parse", source, "parser returned an unexpected object type")
    return summaries


def _probe_fixture(args: argparse.Namespace, debug_dir: Path) -> int:
    """Run the offline fixture verification path (no network)."""
    if args.list_html is None or not args.list_html.exists():
        logger.error("fixture mode requires an existing --list-html file")
        return 2
    list_html = args.list_html.read_text(encoding="utf-8")
    summaries = _parse_summaries(list_html, source=str(args.list_html.resolve()))
    summary = summaries[0]
    logger.info(
        "[PROBE_LIST] fixture=<%s> listings=%d first=<%s>",
        args.list_html.name,
        len(summaries),
        summary.internal_id,
    )
    _write_summary(debug_dir, summary)

    detail: ListingDetail | None = None
    if args.detail_html is not None and args.detail_html.exists():
        detail_html = args.detail_html.read_text(encoding="utf-8")
        detail = _parse_detail(detail_html, source=str(args.detail_html.resolve()))
        _write_detail(debug_dir, detail)
    _print_result(summary, detail)
    return 0


def _probe_url(args: argparse.Namespace, debug_dir: Path) -> int:
    """Run the bounded live path, closing every adapter in all paths."""
    budgets = Budgets(http_timeout_s=args.timeout)
    adapter: HttpDomAdapter | None = None
    detail_adapter: HttpDomAdapter | None = None
    try:
        adapter = HttpDomAdapter(budgets, debug=True)
        list_html = _fetch_safe(adapter, args.url, stage="list_fetch")
        summaries = _parse_summaries(list_html, source=redact_url(args.url))
        summary = summaries[0]
        logger.info(
            "[PROBE_LIST] url=<%s> listings=%d first=<%s>",
            redact_url(args.url),
            len(summaries),
            summary.internal_id,
        )
        _write_summary(debug_dir, summary)

        detail_url = summary.url
        detail_adapter = HttpDomAdapter(budgets, debug=True)
        detail_html = _fetch_safe(detail_adapter, detail_url, stage="detail_fetch")
        detail = _parse_detail(detail_html, source=redact_url(detail_url))
        _write_detail(debug_dir, detail)
        _print_result(summary, detail)
        return 0
    except BlockDetected as block:
        logger.warning(
            "[PROBE_BLOCKED] url=<%s> signature=<%s>", block.redacted_url, block.signature
        )
        return 3
    finally:
        if detail_adapter is not None:
            detail_adapter.close()
        if adapter is not None:
            adapter.close()


def _fetch_safe(adapter: HttpDomAdapter, url: str, *, stage: str) -> str:
    """Fetch ``url``, failing closed on a challenge or block page.

    ``stage`` names the probe step in error diagnostics. Nothing is saved for a
    challenge page; the production boundary never persists one.
    """
    html = adapter.fetch_html(url)
    validate_page_content(html, stage=stage, source=redact_url(url))
    return html


def _parse_detail(html: str, *, source: str) -> ListingDetail:
    """Validate and parse a detail page into a :class:`ListingDetail`."""
    validate_page_content(html, stage="detail_parse", source=source)
    detail = parse_detail_page(html)
    if not isinstance(detail, ListingDetail):
        raise ProbeContentError("detail_parse", source, "parser returned an unexpected object type")
    return detail


def _write_summary(debug_dir: Path, summary: ListingSummary) -> None:
    """Persist a redacted, non-sensitive summary card for the parsed listing."""
    path = safe_artifact_path(debug_dir, "property_summary.txt")
    path.write_text(_summary_card(summary), encoding="utf-8")
    logger.info("[PROBE_ARTIFACT] kind=summary path=<%s>", path.resolve())


def _write_detail(debug_dir: Path, detail: ListingDetail) -> None:
    """Persist a redacted detail card (never challenge HTML or raw pages)."""
    path = safe_artifact_path(debug_dir, "property_detail.txt")
    path.write_text(_detail_card(detail), encoding="utf-8")
    logger.info("[PROBE_ARTIFACT] kind=detail path=<%s>", path.resolve())


def _summary_card(summary: ListingSummary) -> str:
    """Render an operator-safe summary card from a parsed :class:`ListingSummary`."""
    price = summary.price
    return "\n".join(
        [
            f"internal_id: {summary.internal_id}",
            f"title: {summary.title}",
            f"address: {summary.address}",
            f"station: {summary.station or '-'}",
            f"walk_minutes: {summary.walk_minutes if summary.walk_minutes is not None else '-'}",
            f"type: {summary.building_type or '-'}",
            f"floor_plan: {summary.floor_plan or '-'}",
            f"area_m2: {summary.area_m2:g}",
            f"rent: {price.rent:,} yen (mgmt {price.management_fee:,})",
            f"url: {redact_url(summary.url)}",
        ]
    )


def _detail_card(detail: ListingDetail) -> str:
    """Render an operator-safe detail card from a parsed :class:`ListingDetail`."""
    return "\n".join(
        [
            f"internal_id: {detail.internal_id}",
            f"title: {detail.title}",
            f"description: {detail.description[:200] if detail.description else '-'}",
            f"facility_features: {', '.join(detail.facility_features)}",
            f"url: {redact_url(detail.url)}",
        ]
    )


def _print_result(summary: ListingSummary, detail: ListingDetail | None) -> None:
    """Print the operator-facing summary of the parsed property."""
    print(_summary_card(summary))
    if detail is not None:
        print()
        print(_detail_card(detail))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, build settings, and run the selected probe mode."""
    logging.basicConfig(level=logging.INFO)
    args = _parser().parse_args(argv)
    logger.debug("probe args redacted: %s", _redacted_args(args))
    debug_dir = args.debug_dir
    try:
        if args.input_mode == "fixture":
            return _probe_fixture(args, debug_dir)
        return _probe_url(args, debug_dir)
    except ProbeContentError as exc:
        logger.warning("%s", exc)
        return 4
    except (KeyboardInterrupt, SystemExit):
        return 130


if __name__ == "__main__":
    sys.exit(main())
