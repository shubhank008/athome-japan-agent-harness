"""Unit tests for the orchestration CLI (M6, T25).

Covers the :func:`parse_command` REPL parser and every :class:`SearchSession`
handler (search, save, reject, more like, refine) plus the edge cases: plan
confirmation decline, clarification abort, and degradation on block. All
transports are fakes at the LLM/fetch boundaries; parsing, shortlisting,
recommending, persistence, and report writing all run the real code paths.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from athome_harness.cli import SearchSession, SessionDeps, parse_command
from athome_harness.config import Budgets
from athome_harness.llm.base import BaseLLMProvider, LLMUsage
from athome_harness.scraping.base import BlockDetected
from athome_harness.scraping.list_parser import parse_list_page
from athome_harness.store.sqlite_store import SqliteStore
from tests.unit._fakes import build_filter_map

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
LIST_HTML = (FIXTURES / "osaka_rental_list.html").read_text(encoding="utf-8")
_DETAIL_FIXTURES = [
    (FIXTURES / "detail_1101570928.html").read_text(encoding="utf-8"),
    (FIXTURES / "detail_1122949022.html").read_text(encoding="utf-8"),
    (FIXTURES / "detail_1131157822.html").read_text(encoding="utf-8"),
]
EMPTY_LIST_HTML = "<html><body><div class='p-property--building'></div></body></html>"


def _small_list_page(full_html: str, buildings: int) -> str:
    """Derive a small real list page from the first ``buildings`` blocks.

    Keeps the harvest tiny (a dozen listings) so CLI tests exercise the real
    parser / store / LLM funnel without paying the SQLite per-row commit cost
    of the full 460-listing capture.
    """
    tree = HTMLParser(full_html)
    blocks = tree.css("div.p-property--building")
    return "<html><body>" + "".join(b.html for b in blocks[:buildings]) + "</body></html>"


# A ~12-listing page (building 1) keeps each search well under a second.
SMALL_LIST_HTML = _small_list_page(LIST_HTML, 1)
SMALL_LISTINGS = len(parse_list_page(SMALL_LIST_HTML))

_ID_RE = re.compile(r'"internal_id":"([^"]+)"')


class ScriptedProvider(BaseLLMProvider):
    """LLM fake that dispatches on prompt shape and mirrors the input IDs.

    The flow and parser steps return fixed canned intents; the shortlist and
    recommender steps echo back the internal IDs present in their prompt so the
    ranking layers see real, self-consistent inputs (small fake at the LLM
    boundary only).
    """

    def __init__(self) -> None:
        self.calls = 0
        self._usage = LLMUsage(prompt_tokens=10, completion_tokens=5)

    def complete_text(
        self, *, system: str, user: str, temperature: float = 0.0
    ) -> tuple[str, LLMUsage]:
        self.calls += 1
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
        # Parser output step.
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


class FakeFetch:
    """Serves list and detail HTML; can raise a block on a chosen list page."""

    def __init__(self, *, block_on: set[int] | None = None) -> None:
        self._block_on = block_on or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if "PAGENO=" in url:
            page = int(url.split("PAGENO=", 1)[1])
            if page in self._block_on:
                raise BlockDetected(url, "403")
            return SMALL_LIST_HTML if page == 1 else EMPTY_LIST_HTML
        # Cycle distinct detail fixtures so the shortlist produces multiple
        # scraped internal IDs and therefore multiple recommendations.
        idx = sum(1 for c in self.calls if "PAGENO=" not in c) - 1
        return _DETAIL_FIXTURES[idx % len(_DETAIL_FIXTURES)]


def _build_list_url(params: list[tuple[str, str]], page: int) -> str:
    return f"https://example.invalid/athome/list?PAGENO={page}"


def _build_detail_url(summary) -> str:
    return f"https://example.invalid/athome/detail/{summary.internal_id}"


def _make_store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "cli_test.db")


def _make_session(
    tmp_path: Path,
    *,
    budgets: Budgets | None = None,
    block_on: set[int] | None = None,
    confirm=None,
    fetch: FakeFetch | None = None,
) -> tuple[SearchSession, SqliteStore, ScriptedProvider, FakeFetch]:
    store = _make_store(tmp_path)
    provider = ScriptedProvider()
    fetch_impl = fetch or FakeFetch(block_on=block_on)
    deps = SessionDeps(
        provider=provider,
        filter_map=build_filter_map(),
        store=store,
        fetch=fetch_impl,
        build_list_url=_build_list_url,
        build_detail_url=_build_detail_url,
        report_dir=tmp_path / "reports",
        budgets=budgets or Budgets(),
        confirm_plan=confirm,
    )
    return SearchSession(deps), store, provider, fetch_impl


# -- parse_command -----------------------------------------------------------


def test_parse_command_feedback_verbs() -> None:
    assert parse_command("save 3").model_dump() == {"verb": "save", "arg": 3}
    assert parse_command("reject 1").model_dump() == {"verb": "reject", "arg": 1}
    assert parse_command("more like 2").model_dump() == {"verb": "more_like", "arg": 2}
    assert parse_command("refine  cheaper").model_dump() == {"verb": "refine", "arg": "cheaper"}
    assert parse_command("quit").verb == "quit"
    assert parse_command("exit").verb == "quit"


def test_parse_command_falls_back_to_search() -> None:
    assert parse_command("2LDK apartment near station").model_dump() == {
        "verb": "search",
        "arg": "2LDK apartment near station",
    }
    assert parse_command("").verb == "search"
    assert parse_command("save x").verb == "search"


# -- session happy path ------------------------------------------------------


def test_search_happy_path_emits_markers_in_order(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    session, store, _, _ = _make_session(tmp_path)
    outcome = session.search("cheap 2LDK in Osaka")

    assert outcome.status == "ok"
    assert outcome.recommendations and len(outcome.recommendations) >= 1
    sessions = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("[SESSION_START]")
    ]
    assert len(sessions) == 1
    # Every happy-path marker present once and in contract order.
    order = [
        "[SESSION_START]",
        "[SEARCH_PLAN]",
        "[FILTER_ENCODE]",
        "[HARVEST_START]",
        "[HARVEST_PAGE]",
        "[HARVEST_DONE]",
        "[SHORTLIST_START]",
        "[SHORTLIST_DONE]",
        "[DETAIL_START]",
        "[DETAIL_DONE]",
        "[REPORT]",
        "[STORE]",
        "[SESSION_END]",
    ]
    messages = [r.getMessage() for r in caplog.records]
    positions = [next(i for i, m in enumerate(messages) if m.startswith(mark)) for mark in order]
    assert positions == sorted(positions), "markers out of order"
    assert any(m.startswith("[SESSION_END] status=ok") for m in messages)

    # Report files exist and parse.
    report_dir = tmp_path / "reports"
    md_files = list(report_dir.glob("report-*.md"))
    json_files = list(report_dir.glob("report-*.json"))
    assert md_files and json_files
    md_files[0].read_text(encoding="utf-8")
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert isinstance(payload["recommendations"], list)
    assert payload["recommendations"]

    # Store has collected the harvested listings and the search.
    assert len(store.seen_internal_ids()) == SMALL_LISTINGS
    assert store.search_history(limit=1)
    assert session.last_query == "cheap 2LDK in Osaka"


def test_search_clarification_aborts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, _, provider, _ = _make_session(tmp_path)

    # Replace parser output with an ambiguous one on the 2nd call.
    def ambiguous(system, user, temperature=0.0):
        if "Return the search flow" in system:
            return json.dumps({"flow": "rent"}), LLMUsage(prompt_tokens=1, completion_tokens=1)
        if "Preferences:" in user or "Constraints:" in user:
            return json.dumps(
                {"entries": []} if "Preferences:" in user else {"ranked": []}
            ), LLMUsage(prompt_tokens=1, completion_tokens=1)
        return (
            json.dumps(
                {
                    "flow": "rent",
                    "prefecture": "osaka",
                    "ambiguous": True,
                    "clarification_question": "Which area?",
                    "hard_filters": {},
                    "soft_prefs": [],
                }
            ),
            LLMUsage(prompt_tokens=1, completion_tokens=1),
        )

    provider.complete_text = ambiguous  # type: ignore[method-assign]
    outcome = session.search("mystery query")

    assert outcome.status == "aborted"
    assert outcome.clarifying_question == "Which area?"
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        m.startswith("[SEARCH_PLAN] hard_filters=0 soft_prefs=0 ambiguous=true") for m in messages
    )
    assert any(m.startswith("[CLARIFY] question=") for m in messages)
    assert any(m.startswith("[SESSION_END] status=aborted") for m in messages)


def test_plan_confirm_decline_aborts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, _, _, _ = _make_session(tmp_path, confirm=lambda query, plan: False)

    outcome = session.search("cheap 2LDK")
    assert outcome.status == "aborted"
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[SESSION_END] status=aborted") for m in messages)
    # No harvesting happened on decline.
    assert not any(m.startswith("[HARVEST_DONE]") for m in messages)


def test_block_degrades_to_partial(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, _, _, _ = _make_session(tmp_path, block_on={1})

    outcome = session.search("cheap 2LDK")
    assert outcome.status == "partial"
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[BLOCK_DETECTED]") for m in messages)
    assert any(m.startswith("[PARTIAL_REPORT] reason=block") for m in messages)
    assert any(m.startswith("[SESSION_END] status=partial") for m in messages)
    # No recommendations can be produced from an empty harvest.
    assert outcome.recommendations == []


# -- feedback handlers -------------------------------------------------------


def test_save_and_reject_then_refine(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, store, _, _ = _make_session(tmp_path)
    session.search("cheap 2LDK")

    assert session.save(1) == 1
    assert session.reject(2) == 1
    saved = store.saved_internal_ids()
    rejected = store.rejected_internal_ids()
    assert len(saved) == 1 and len(rejected) == 1
    rec1 = session.last_recommendations[0].listing
    rec2 = session.last_recommendations[1].listing
    assert rec1 and rec1.internal_id in saved
    assert rec2 and rec2.internal_id in rejected


def test_save_reject_out_of_range_are_noops(tmp_path: Path) -> None:
    session, _, _, _ = _make_session(tmp_path)
    session.search("cheap 2LDK")
    assert session.save(999) == 0
    assert session.reject(-1) == 0


def test_more_like_returns_recommendations(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    session, _, _, _ = _make_session(tmp_path)
    session.search("cheap 2LDK")

    search_md = list((tmp_path / "reports").glob("report-*.md"))
    search_json = list((tmp_path / "reports").glob("report-*.json"))
    search_md_content = search_md[0].read_text(encoding="utf-8")

    recs = session.more_like(1)
    assert recs, "more_like should return a fresh ranking"
    assert len(recs) >= 1

    after_more_md = sorted((tmp_path / "reports").glob("report-*.md"))
    after_more_json = sorted((tmp_path / "reports").glob("report-*.json"))
    assert len(after_more_md) == len(search_md) + 1, "more_like must not overwrite search report"
    assert len(after_more_json) == len(search_json) + 1
    assert search_md[0].read_text(encoding="utf-8") == search_md_content


def test_refine_runs_new_search(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, _, _, _ = _make_session(tmp_path)
    first = session.search("cheap")
    session.refine("2LDK")

    assert first.session_id != session.last_session_id
    assert session.last_query == "cheap 2LDK"
