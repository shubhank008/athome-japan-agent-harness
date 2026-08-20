"""Bounded operator probe for the full search-run funnel.

Composes every step of the production pipeline in the same order as
:class:`~athome_harness.cli.SearchSession`: query parser -> filter encoder ->
harvester -> shortlister -> detail scraping -> recommender -> report rendering
-> store boundaries. It works two ways:

* Default (``--mode fixture``): runs the entire funnel over captured fixture
  HTML and a canned LLM with no network at all. This is deterministic, offline,
  and safe to run anywhere.
* Opt-in (``--mode live``): builds the production transports through the
  provider factory (LLM, store, and the ``SessionRefarmer`` fallback fetch) and
  runs one real search. Live mode never bypasses challenge handling or
  ``SessionRefarmer``; it simply composes the same production fetch path a human
  uses, so a block degrades to a partial report.

Both modes use the real :class:`SearchSession`, real parsers, real store, and
real report rendering. Temporary outputs (the report directory and any session
store) are written under a throwaway location and removed on exit unless
``--keep-outputs`` is set.

RUN (offline):  PYTHONPATH=src python scripts/full_run_probe.py \\
                     --query "cheap 2LDK in Osaka"
RUN (live):     PYTHONPATH=src python scripts/full_run_probe.py \\
                     --mode live --query "cheap 2LDK in Osaka"
HELP (no net):  PYTHONPATH=src python scripts/full_run_probe.py --help
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from athome_harness.cli import SearchSession, SessionDeps  # noqa: E402
from athome_harness.config import Budgets  # noqa: E402
from athome_harness.llm.base import BaseLLMProvider, LLMUsage  # noqa: E402
from athome_harness.models import FilterMap  # noqa: E402
from athome_harness.store.sqlite_store import SqliteStore  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_QUERY = "cheap 2LDK in Osaka"
FIXTURES = _REPO_ROOT / "tests" / "fixtures"
_ID_RE = re.compile(r'"internal_id":"([^"]+)"')


def _parser() -> argparse.ArgumentParser:
    """Build the full-run probe command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default="fixture",
        help="fixture: offline over fixture HTML; live: real network (opt-in).",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Natural-language query.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".probe-work"),
        help="Throwaway directory for reports and the session store (removed on exit).",
    )
    parser.add_argument(
        "--keep-outputs",
        action="store_true",
        help="Keep the work directory instead of removing it on exit.",
    )
    return parser


# ---------------------------------------------------------------------------
# Fixture (offline) dependencies
# ---------------------------------------------------------------------------


class ScriptedProvider(BaseLLMProvider):
    """Canned LLM that dispatches on prompt shape (port of the e2e fake)."""

    def __init__(self) -> None:
        self._usage = LLMUsage(prompt_tokens=10, completion_tokens=5)

    def complete_text(
        self, *, system: str, user: str, temperature: float = 0.0
    ) -> tuple[str, LLMUsage]:
        ids = _ID_RE.findall(user)
        if "Return the search flow" in system:
            return '{"flow":"rent"}', self._usage
        if "Preferences:" in user:
            selected = list(dict.fromkeys(ids))[:5]
            entries = [
                {"listing_id": lid, "score": 9.0, "rationale": "good fit"} for lid in selected
            ]
            return json.dumps({"entries": entries}), self._usage
        if "Constraints:" in user:
            ranked = [
                {
                    "listing_id": lid,
                    "reasons": ["candidate"],
                    "satisfied_constraints": ["cheap"],
                    "violated_constraints": [],
                }
                for lid in list(dict.fromkeys(ids))[:5]
            ]
            return json.dumps({"ranked": ranked}), self._usage
        return (
            json.dumps(
                {
                    "flow": "rent",
                    "prefecture": "osaka",
                    "cities": [],
                    "ambiguous": False,
                    "clarification_question": None,
                    "hard_filters": {},
                    "soft_prefs": ["cheap", "2LDK"],
                }
            ),
            self._usage,
        )


def _small_list_page(full_html: str, buildings: int) -> str:
    """Derive a small real list page from the first ``buildings`` blocks."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(full_html)
    blocks = tree.css("div.p-property--building")
    return "<html><body>" + "".join(b.html or "" for b in blocks[:buildings]) + "</body></html>"


class FakeFetch:
    """Serves list and detail HTML from fixture files (never the network).

    Returns a non-listing empty page for result page 2+ so the harvest ends
    naturally (matching the e2e test) instead of exhausting the page budget.
    """

    def __init__(self, list_html: str, detail_html: list[str]) -> None:
        self._list_html = list_html
        self._detail_html = detail_html
        self._empty_list_html = "<html><body><div class='p-property--building'></div></body></html>"
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if "PAGENO=" in url:
            page = int(url.split("PAGENO=", 1)[1])
            return self._list_html if page == 1 else self._empty_list_html
        idx = sum(1 for c in self.calls if "PAGENO=" not in c) - 1
        return self._detail_html[idx % len(self._detail_html)]


def _fixture_deps(work_dir: Path) -> tuple[SessionDeps, SqliteStore]:
    """Build fully injected fixture deps (offline, deterministic, real code)."""
    from tests.unit._fakes import build_filter_map

    list_html = _small_list_page(
        (FIXTURES / "osaka_rental_list.html").read_text(encoding="utf-8"), 2
    )
    detail_html = [
        (FIXTURES / "detail_1101570928.html").read_text(encoding="utf-8"),
        (FIXTURES / "detail_1122949022.html").read_text(encoding="utf-8"),
        (FIXTURES / "detail_1131157822.html").read_text(encoding="utf-8"),
    ]
    store = SqliteStore(work_dir / "probe.db")
    provider = ScriptedProvider()
    fetch = FakeFetch(list_html, detail_html)
    deps = SessionDeps(
        provider=provider,
        filter_map=build_filter_map(),
        store=store,
        fetch=fetch,
        build_list_url=lambda params, page: f"https://example.invalid/list?PAGENO={page}",
        build_detail_url=lambda summary: f"https://example.invalid/detail/{summary.internal_id}",
        report_dir=work_dir / "reports",
        budgets=Budgets(),
    )
    return deps, store


# ---------------------------------------------------------------------------
# Live (opt-in) dependencies
# ---------------------------------------------------------------------------


def _live_deps(work_dir: Path) -> SessionDeps:
    """Build production deps (LLM, store, SessionRefarmer fetch) from settings."""
    from athome_harness.providers import (
        build_llm_provider,
        build_production_fetch,
        build_store,
        load_settings,
    )

    settings = load_settings()
    filter_map = _load_filter_map()
    store = build_store(settings)
    report_dir = work_dir / "reports"
    return SessionDeps(
        provider=build_llm_provider(settings),
        filter_map=filter_map,
        store=store,
        fetch=build_production_fetch(settings.budgets, settings),
        build_list_url=_default_list_url,
        build_detail_url=lambda summary: summary.url,
        report_dir=report_dir,
        budgets=settings.budgets,
        confirm_plan=lambda query, plan: True,
    )


def _load_filter_map() -> FilterMap:
    """Load the checked-in versioned filter map from ``filters/data``."""
    import json

    from athome_harness.filters.map_schema import validate

    payload = json.loads(
        (_REPO_ROOT / "filters" / "data" / "filter_map.v1.json").read_text(encoding="utf-8")
    )
    return validate(FilterMap.model_validate(payload))


def _default_list_url(params: list[tuple[str, str]], page: int) -> str:
    """Build an AtHome rental list URL carrying encoded params and page number."""
    query = "&".join(f"{name}={value}" for name, value in params)
    return f"https://www.athome.co.jp/chintai/osaka/list/?{query}&PAGENO={page}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _run_session(query: str, deps: SessionDeps, store: SqliteStore | None) -> int:
    """Run one full search through the real :class:`SearchSession` and report."""
    session = SearchSession(deps)
    outcome = session.search(query)
    print(f"status: {outcome.status} session_id={outcome.session_id}")
    if outcome.clarifying_question:
        print(f"clarifying: {outcome.clarifying_question}")
        return 0
    report = outcome.report
    if report is None:
        print("no report produced")
        return 4
    print(f"results_seen: {report.results_seen} pages_scraped: {report.pages_scraped}")
    print(f"shortlist: {len(report.shortlist)} recommendations: {len(report.recommendations)}")
    print(f"partial: {report.partial}")
    for rec in report.recommendations:
        listing = rec.listing
        title = listing.title if listing is not None else rec.listing_id
        print(f"  {rec.rank}. {title}  ({rec.satisfied_constraints})")
    for path in sorted(deps.report_dir.glob("report-*.md")):
        print(f"report: {path.resolve()}")
    if store is not None:
        searches = len(store.search_history())
        seen = len(store.seen_internal_ids())
        print(f"store searches: {searches} seen: {seen}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, build deps, run the funnel, and clean up outputs."""
    logging.basicConfig(level=logging.INFO)
    args = _parser().parse_args(argv)
    work_dir = args.work_dir.resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    store: SqliteStore | None = None
    deps: SessionDeps | None = None
    try:
        if args.mode == "fixture":
            deps, store = _fixture_deps(work_dir)
        else:
            deps = _live_deps(work_dir)
            store = deps.store if isinstance(deps.store, SqliteStore) else None
        return _run_session(args.query, deps, store)
    finally:
        if deps is not None:
            deps.store.close()
        if not args.keep_outputs:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
