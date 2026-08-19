"""Conversational orchestration and CLI entry point (milestone M6, T25).

Wires the M1-M5 abstractions into the public end-to-end loop:

    QueryParser -> plan confirm -> harvest -> top-X preview -> detail scrape
    -> Recommender -> report -> feedback commands (save/reject/more like/refine)

The loop is exposed two ways:

* :class:`SearchSession`, a typed, dependency-injected API that is easy to
  exercise programmatically (unit tests and the scripted e2e test drive this)
  and emits every marker required by the contract.
* :func:`parse_command` / :func:`interactive`, a thin human REPL over the same
  :class:`SearchSession`.

Per the Abstract First invariant, all transports are injected: the LLM funnel
interfaces (:class:`BaseLLMProvider` plus its wrappers), the fetch callable
(page HTML for list and detail), the store, the filter map, the report
directory, the clock, and the plan-confirmation callback. No third-party
transport is imported here. Tests inject fakes only at these boundaries; the
live network path is built by :func:`providers.build_production_fetch` for human
use and is never exercised by unit or e2e tests.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from athome_harness.config import Budgets
from athome_harness.filters.encoder import UnknownFilter, UnknownFilterValue, encode_plan
from athome_harness.llm.base import BaseLLMProvider
from athome_harness.llm.query_parser import ClarificationNeeded, QueryParser
from athome_harness.llm.recommender import Recommender, render_json, render_markdown
from athome_harness.llm.shortlister import Shortlister
from athome_harness.models import (
    FilterMap,
    ListingDetail,
    ListingSummary,
    Recommendation,
    RunReport,
    SearchPlan,
)
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.harvester import Harvester
from athome_harness.scraping.list_parser import parse_list_page
from athome_harness.store.base import BaseDataStore

logger = logging.getLogger(__name__)

# How many "be like N" soft preferences to fold in when re-ranking a target.
_MORE_LIKE_PREF_TEMPLATE = "similar to {title}"


@dataclass
class SessionDeps:
    """Injected boundaries for one :class:`SearchSession`.

    Every field is a seam: tests supply small fakes for the fetch, clock, and
    confirmation callables while production builds real ones. ``build_list_url``
    maps ``(encoded params, 1-based page)`` to a URL; ``build_detail_url`` maps a
    summary to its detail-page URL.
    """

    provider: BaseLLMProvider
    filter_map: FilterMap
    store: BaseDataStore
    fetch: Callable[[str], str]
    build_list_url: Callable[[list[tuple[str, str]], int], str]
    build_detail_url: Callable[[ListingSummary], str]
    report_dir: Path
    budgets: Budgets = field(default_factory=Budgets)
    clock: Callable[[], float] | None = None
    confirm_plan: Callable[[str, SearchPlan], bool] | None = None
    detail_parser: Callable[[str], ListingDetail] | None = None


@dataclass
class SearchOutcome:
    """Result of one full search run, plus the session's last outputs."""

    status: str
    session_id: str
    report: RunReport | None = None
    shortlist: list[ListingSummary] | None = None
    recommendations: list[Recommendation] | None = None
    clarifying_question: str | None = None


# ---------------------------------------------------------------------------
# Command parsing for the human REPL and feedback handlers
# ---------------------------------------------------------------------------


class Command(BaseModel):
    """One parsed REPL command: a verb plus an optional argument."""

    verb: str = Field(description="Command verb: save, reject, more_like, refine, quit, search.")
    arg: str | int | None = Field(default=None, description="Command argument, when any.")


def parse_command(text: str) -> Command:
    """Parse a trimmed REPL line into a :class:`Command`.

    Recognises ``save N``, ``reject N``, ``more like N``, ``refine <clause>``,
    and ``quit``/``exit``. Anything not matching a feedback verb is treated as a
    plain search query.
    """
    stripped = text.strip()
    lower = stripped.lower()
    if lower in {"quit", "exit", "q"}:
        return Command(verb="quit")
    for verb, prefixes in (("save", ("save ",)), ("reject", ("reject ",))):
        for prefix in prefixes:
            if lower.startswith(prefix):
                tail = stripped[len(prefix) :].strip()
                if tail.isdigit():
                    return Command(verb=verb, arg=int(tail))
    if lower.startswith("more like "):
        tail = stripped[len("more like ") :].strip()
        if tail.isdigit():
            return Command(verb="more_like", arg=int(tail))
    if lower.startswith("refine"):
        clause = stripped[len("refine") :].strip()
        if clause:
            return Command(verb="refine", arg=clause)
    if not stripped:
        return Command(verb="search", arg=None)
    return Command(verb="search", arg=stripped)


# ---------------------------------------------------------------------------
# The session orchestrator
# ---------------------------------------------------------------------------


class SearchSession:
    """Runs the full search pipeline and the feedback commands.

    Construct with a :class:`SessionDeps`. The same instance persists the last
    query, plan, shortlist, and recommendations so feedback commands (save,
    reject, more like, refine) operate against them.
    """

    def __init__(self, deps: SessionDeps) -> None:
        self._deps = deps
        self._parser = QueryParser(deps.provider, deps.filter_map)
        self._shortlister = Shortlister(deps.provider)
        self._recommender = Recommender(deps.provider)
        self._clock: Callable[[], float] = deps.clock or (lambda: 0.0)
        self._confirm = deps.confirm_plan or (lambda query, plan: True)
        self._detail_parser = deps.detail_parser or (lambda html: parse_detail_page(html))

        # Last-run state consumed by feedback commands.
        self.last_query: str | None = None
        self.last_plan: SearchPlan | None = None
        self.last_shortlist: list[ListingSummary] = []
        self.last_recommendations: list[Recommendation] = []
        self.last_harvest: list[ListingSummary] = []
        self.last_session_id: str | None = None

        deps.report_dir.mkdir(parents=True, exist_ok=True)

    # -- Public pipeline ----------------------------------------------------

    def search(self, query: str) -> SearchOutcome:
        """Run the full loop for ``query`` and return its outcome.

        Emits every happy-path marker in contract order and degrades to
        ``partial`` or ``aborted`` (with the corresponding degradation markers
        from the harvester) when a block or budget stops the run.
        """
        session_id = str(uuid.uuid4())
        started = self._clock()
        logger.info("[SESSION_START] session_id=<%s> flow=unknown prefecture=unknown", session_id)

        if not query.strip():
            self._end_session(started, "aborted")
            return SearchOutcome(status="aborted", session_id=session_id)

        # Parse the natural-language query into a plan.
        try:
            plan = self._parser.parse(query)
        except ClarificationNeeded as exc:
            logger.info("[SEARCH_PLAN] hard_filters=0 soft_prefs=0 ambiguous=true")
            logger.info("[CLARIFY] question=<%s>", exc.question)
            self._end_session(started, "aborted")
            return SearchOutcome(
                status="aborted",
                session_id=session_id,
                clarifying_question=exc.question,
            )
        logger.info(
            "[SEARCH_PLAN] hard_filters=%d soft_prefs=%d ambiguous=false",
            len(plan.hard_filters),
            len(plan.soft_prefs),
        )

        # Present the plan and let the user (or a test callback) confirm it.
        if not self._confirm(query, plan):
            self._end_session(started, "aborted")
            return SearchOutcome(status="aborted", session_id=session_id)

        # Encode the plan to POST params (logs [FILTER_ENCODE] on success).
        try:
            params = encode_plan(plan, self._deps.filter_map)
        except UnknownFilter as exc:
            logger.error(
                "[SESSION_END] status=aborted elapsed_s=%.3f error=<UnknownFilter>",
                self._elapsed(started),
            )
            logger.error("[UNKNOWN_FILTER_ENCODED] filter=<%s>", exc)
            return SearchOutcome(status="aborted", session_id=session_id)
        except UnknownFilterValue as exc:
            logger.error(
                "[SESSION_END] status=aborted elapsed_s=%.3f error=<UnknownFilterValue>",
                self._elapsed(started),
            )
            logger.error("[UNKNOWN_FILTER_ENCODED] value=<%s>", exc)
            return SearchOutcome(status="aborted", session_id=session_id)

        # Harvest the results pages.
        harvester = Harvester(
            fetch_page=self._deps.fetch,
            parse_page=parse_list_page,
            build_page_url=lambda page: self._deps.build_list_url(params, page),
            budgets=self._deps.budgets,
            clock=self._clock,
        )
        pre_seen = self._deps.store.seen_internal_ids()
        harvest = harvester.harvest()
        self.last_harvest = list(harvest.listings)

        # Persist harvested listings and record the search.
        for listing in harvest.listings:
            self._deps.store.upsert_listing(listing)
        search_id = self._deps.store.record_search(query, plan)

        # Shortlist: top-X preview over non-rejected candidate listings.
        candidates = [
            listing
            for listing in harvest.listings
            if listing.internal_id not in self._deps.store.rejected_internal_ids()
        ]
        logger.info(
            "[SHORTLIST_START] candidates=%d batch_size=%d",
            len(candidates),
            self._deps.budgets.shortlist_size,
        )
        shortlisted = self._shortlister.shortlist(
            plan.soft_prefs,
            candidates,
            top_x=self._deps.budgets.shortlist_size,
        )
        logger.info(
            "[SHORTLIST_DONE] shortlisted=%d tokens=%d",
            len(shortlisted),
            self._deps.provider.total_tokens,
        )
        by_id = {listing.internal_id: listing for listing in harvest.listings}
        shortlist_summaries = [by_id[e.listing_id] for e in shortlisted if e.listing_id in by_id]
        self.last_shortlist = shortlist_summaries

        # Scrape the details of the shortlist targets.
        logger.info("[DETAIL_START] targets=%d", len(shortlist_summaries))
        details, failed = self._scrape_details(shortlist_summaries)
        logger.info("[DETAIL_DONE] scraped=%d failed=%d", len(details), failed)

        # Rank the details into top-Y recommendations.
        recommendations = self._recommender.recommend(
            details,
            plan,
            top_y=self._deps.budgets.recommendations_count,
        )
        self.last_recommendations = recommendations

        # Render and persist the report files.
        md_path, json_path = self._write_report(query, recommendations, session_id=session_id)

        # Record recommendations and feedback-facing store state.
        self._deps.store.record_recommendation(search_id, recommendations)
        seen = len(self._deps.store.seen_internal_ids())
        rejected_ids = self._deps.store.rejected_internal_ids()
        new = sum(1 for listing in harvest.listings if listing.internal_id not in pre_seen)
        rejected_excluded = sum(
            1 for listing in harvest.listings if listing.internal_id in rejected_ids
        )
        logger.info("[STORE] seen=%d new=%d rejected_excluded=%d", seen, new, rejected_excluded)

        report = RunReport(
            query=query,
            plan=plan,
            results_seen=len(harvest.listings),
            pages_scraped=harvest.pages_scraped,
            shortlist=shortlist_summaries,
            recommendations=recommendations,
            budgets_consumed=self._deps.budgets,
            partial=harvest.partial,
        )
        self.last_query = query
        self.last_plan = plan
        self.last_session_id = session_id
        status = "partial" if harvest.partial else "ok"
        self._end_session(started, status)
        return SearchOutcome(
            status=status,
            session_id=session_id,
            report=report,
            shortlist=shortlist_summaries,
            recommendations=recommendations,
        )

    # -- Feedback commands --------------------------------------------------

    def save(self, rank: int) -> int:
        """Save the listing at 1-based ``rank`` in the last recommendations."""
        listing = self._listing_at_rank(rank)
        if listing is None:
            return 0
        self._deps.store.save_listing(listing.internal_id)
        return 1

    def reject(self, rank: int) -> int:
        """Reject the listing at 1-based ``rank`` in the last recommendations."""
        listing = self._listing_at_rank(rank)
        if listing is None:
            return 0
        self._deps.store.reject_listing(listing.internal_id)
        return 1

    def more_like(self, rank: int) -> list[Recommendation]:
        """Return a fresh top-Y ranking biased toward the listing at ``rank``.

        Uses the last plan's soft preferences plus a "similar to N" preference
        drawn from the target listing's own features, then re-runs shortlist,
        detail scrape, and recommend over the current non-rejected harvest pool.
        """
        target = self._listing_at_rank(rank)
        if target is None or self.last_plan is None:
            return []
        prefs = list(self.last_plan.soft_prefs)
        prefs.append(_MORE_LIKE_PREF_TEMPLATE.format(title=target.title))
        candidates = [
            listing
            for listing in self.last_harvest
            if listing.internal_id not in self._deps.store.rejected_internal_ids()
        ]
        shortlisted = self._shortlister.shortlist(
            prefs, candidates, top_x=self._deps.budgets.shortlist_size
        )
        by_id = {listing.internal_id: listing for listing in self.last_harvest}
        targets = [by_id[e.listing_id] for e in shortlisted if e.listing_id in by_id]
        details, _ = self._scrape_details(targets)
        recommendations = self._recommender.recommend(
            details,
            self.last_plan,
            top_y=self._deps.budgets.recommendations_count,
        )
        self.last_recommendations = recommendations
        more_session_id = f"more-like-{uuid.uuid4()}"
        report_query = f"{self.last_query or ''} more like {rank}"
        self._write_report(report_query, recommendations, session_id=more_session_id)
        return recommendations

    def refine(self, clause: str) -> SearchOutcome:
        """Re-run a search with ``clause`` appended to the previous query."""
        base = self.last_query or ""
        combined = f"{base} {clause}".strip()
        return self.search(combined)

    # -- Internal helpers ---------------------------------------------------

    def _scrape_details(
        self, summaries: Sequence[ListingSummary]
    ) -> tuple[list[ListingDetail], int]:
        """Fetch and parse the detail page of each summary.

        Returns ``(details, failed)`` where failures are counted but never stop
        the run, so partial detail failures stay bounded and useful.
        """
        details: list[ListingDetail] = []
        failed = 0
        for summary in summaries:
            try:
                html = self._deps.fetch(self._deps.build_detail_url(summary))
                details.append(self._detail_parser(html))
            except Exception:  # noqa: BLE001 - a single detail failure degrades
                failed += 1
        return details, failed

    def _listing_at_rank(self, rank: int) -> ListingSummary | None:
        """Return the summary embedded in the last recommendation at ``rank``."""
        if rank < 1 or rank > len(self.last_recommendations):
            return None
        rec = self.last_recommendations[rank - 1]
        return rec.listing

    def _write_report(
        self,
        query: str,
        recommendations: list[Recommendation],
        *,
        session_id: str | None = None,
    ) -> tuple[Path, Path]:
        """Write markdown and JSON reports, return their paths."""
        sid = session_id or self.last_session_id or "session"
        md = self._deps.report_dir / f"report-{sid}.md"
        js = self._deps.report_dir / f"report-{sid}.json"
        md.write_text(render_markdown(recommendations, query=query), encoding="utf-8")
        js.write_text(render_json(recommendations), encoding="utf-8")
        logger.info(
            "[REPORT] top_y=%d md=<%s> json=<%s>",
            len(recommendations),
            md,
            js,
        )
        return md, js

    def _end_session(self, started: float, status: str) -> None:
        """Emit the mandatory ``[SESSION_END]`` marker."""
        logger.info(
            "[SESSION_END] status=%s elapsed_s=%.3f total_tokens=%d",
            status,
            self._elapsed(started),
            self._deps.provider.total_tokens,
        )

    def _elapsed(self, started: float) -> float:
        """Seconds elapsed since ``started`` under the injected clock."""
        return self._clock() - started


# ---------------------------------------------------------------------------
# Human REPL and production fetch wiring
# ---------------------------------------------------------------------------


def interactive() -> None:
    """Run the human REPL loop on stdin/stdout.

    Prompts for a query or a feedback command, runs the pipeline, and keeps the
    session alive so later commands (save/reject/more like/refine) operate on
    the last search. This is the entry point for ``python -m athome_harness.cli``.
    """
    from athome_harness.providers import (
        build_llm_provider,
        build_production_fetch,
        build_store,
        load_settings,
    )

    settings = load_settings()
    filter_map = _load_filter_map()
    store = build_store(settings)
    deps = SessionDeps(
        provider=build_llm_provider(settings),
        filter_map=filter_map,
        store=store,
        fetch=build_production_fetch(settings.budgets, settings),
        build_list_url=_default_list_url,
        build_detail_url=lambda summary: summary.url,
        report_dir=Path("reports"),
        budgets=settings.budgets,
    )
    session = SearchSession(deps)

    print(
        "AtHome home finder. Type a query, or save N / reject N / more like N / refine ... / quit."
    )
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        command = parse_command(line)
        if command.verb == "quit":
            break
        if command.verb == "search":
            query = str(command.arg or "")
            outcome = session.search(query)
            _print_outcome(outcome)
        elif command.verb == "save" and isinstance(command.arg, int):
            session.save(command.arg)
        elif command.verb == "reject" and isinstance(command.arg, int):
            session.reject(command.arg)
        elif command.verb == "more_like" and isinstance(command.arg, int):
            recs = session.more_like(command.arg)
            print(f"returned {len(recs)} more-like recommendations")
        elif command.verb == "refine" and isinstance(command.arg, str):
            _print_outcome(session.refine(command.arg))
    store.close()


def _default_list_url(params: list[tuple[str, str]], page: int) -> str:
    """Build an AtHome rental list URL carrying encoded params and page number."""
    query = "&".join(f"{name}={value}" for name, value in params)
    return f"https://www.athome.co.jp/chintai/osaka/list/?{query}&PAGENO={page}"


def _load_filter_map() -> FilterMap:
    """Load the checked-in versioned filter map from ``filters/data``."""
    import json

    from athome_harness.filters.map_schema import validate

    payload = json.loads(Path("filters/data/filter_map.v1.json").read_text(encoding="utf-8"))
    return validate(FilterMap.model_validate(payload))


def _print_outcome(outcome: SearchOutcome) -> None:
    """Print a concise human summary of a search outcome."""
    if outcome.status == "aborted":
        if outcome.clarifying_question:
            print(f"Need clarification: {outcome.clarifying_question}")
        else:
            print("Search aborted.")
        return
    print(f"status={outcome.status} recommendations={len(outcome.recommendations or [])}")
    for rec in outcome.recommendations or []:
        listing = rec.listing
        title = listing.title if listing is not None else rec.listing_id
        print(f"  {rec.rank}. {title}  ({rec.satisfied_constraints})")


def main() -> None:
    """Console-script entry point that runs the interactive REPL."""
    interactive()


if __name__ == "__main__":
    main()
