"""Scripted human-like search session against fixtures and a fake LLM (M6, T26).

Drives the full public loop (:class:`~athome_harness.cli.SearchSession`) the
way a human terminal user would: run a search, save a pick, reject another,
ask for more like an option, then refine the query. Everything runs on fixture
HTML and a fake :class:`~athome_harness.llm.base.BaseLLMProvider`; no live
network or challenge-solving is involved.

Assertions reflect the marker contract in
``docs/specs/001-athome-home-finder/contracts/log-markers.md``: every happy
path marker must appear (in order), the forbidden failure patterns must never
appear, and the produced report files must exist and parse.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser

from athome_harness.cli import SearchSession, SessionDeps
from athome_harness.config import Budgets
from athome_harness.llm.base import BaseLLMProvider, LLMUsage
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

_ID_RE = re.compile(r'"internal_id":"([^"]+)"')

# Contract marker names for the happy path, in the order they must appear.
HAPPY_PATH_MARKERS = [
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

# Failure patterns that must never appear in any test run (contract).
FORBIDDEN_PATTERNS = [
    "Traceback (most recent call last)",
    "LLM_JSON_INVALID",
    "UNKNOWN_FILTER_ENCODED",
    "PROXY_CREDENTIALS_IN_URL_LOG",
    "FILTER_MAP_SCHEMA_UNSUPPORTED",
]


def _small_list_page(full_html: str, buildings: int) -> str:
    """Derive a small real list page from the first ``buildings`` blocks."""
    tree = HTMLParser(full_html)
    blocks = tree.css("div.p-property--building")
    return "<html><body>" + "".join(b.html or "" for b in blocks[:buildings]) + "</body></html>"


# A two-building page (13 listings) keeps the session fast while still driving
# the real parser, store, and LLM funnel against fixture HTML.
SMALL_LIST_HTML = _small_list_page(LIST_HTML, 2)


class ScriptedProvider(BaseLLMProvider):
    """LLM fake that dispatches on prompt shape and mirrors the input IDs."""

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
    """Serves list and detail HTML from fixture files (never the network)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if "PAGENO=" in url:
            page = int(url.split("PAGENO=", 1)[1])
            return SMALL_LIST_HTML if page == 1 else EMPTY_LIST_HTML
        idx = sum(1 for c in self.calls if "PAGENO=" not in c) - 1
        return _DETAIL_FIXTURES[idx % len(_DETAIL_FIXTURES)]


def _build_list_url(params: list[tuple[str, str]], page: int) -> str:
    return f"https://example.invalid/athome/list?PAGENO={page}"


def _build_detail_url(summary) -> str:
    return f"https://example.invalid/athome/detail/{summary.internal_id}"


def _make_session(tmp_path: Path) -> tuple[SearchSession, SqliteStore, ScriptedProvider, FakeFetch]:
    """Build a fully injected session over fixture HTML and a fake LLM."""
    store = SqliteStore(tmp_path / "e2e.db")
    provider = ScriptedProvider()
    fetch = FakeFetch()
    deps = SessionDeps(
        provider=provider,
        filter_map=build_filter_map(),
        store=store,
        fetch=fetch,
        build_list_url=_build_list_url,
        build_detail_url=_build_detail_url,
        report_dir=tmp_path / "reports",
        budgets=Budgets(),
    )
    return SearchSession(deps), store, provider, fetch


def test_scripted_human_session(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    session, store, provider, _ = _make_session(tmp_path)

    # --- first search, as a human would type it ---
    outcome = session.search("cheap 2LDK in Osaka")
    assert outcome.status == "ok"
    assert outcome.recommendations

    # --- feedback commands ---
    assert session.save(1) == 1
    assert session.reject(2) == 1
    saved = store.saved_internal_ids()
    rejected = store.rejected_internal_ids()
    assert len(saved) == 1 and len(rejected) == 1

    # more like a pick returns a fresh ranking and writes a report
    more_like = session.more_like(1)
    assert more_like
    assert list((tmp_path / "reports").glob("report-*.md"))

    # refine runs a brand-new search with an extended query
    first_id = outcome.session_id
    refined = session.refine("closer to station")
    assert refined.status == "ok"
    assert refined.session_id != first_id
    assert session.last_query == "cheap 2LDK in Osaka closer to station"

    # --- marker contract assertions over the whole scripted session ---
    messages = [r.getMessage() for r in caplog.records]
    # 1) every happy-path marker present, in relative order
    present = [m for m in HAPPY_PATH_MARKERS if any(msg.startswith(m) for msg in messages)]
    assert present == HAPPY_PATH_MARKERS, "missing happy-path markers"
    positions = [
        next(i for i, msg in enumerate(messages) if msg.startswith(m)) for m in HAPPY_PATH_MARKERS
    ]
    assert positions == sorted(positions), "happy-path markers out of order"
    # two full searches ran (refine produced a second SESSION_START..SESSION_END)
    assert sum(1 for m in messages if m.startswith("[SESSION_START]")) == 2
    assert sum(1 for m in messages if m.startswith("[SESSION_END] status=ok")) == 2
    # 2) happy path stays clean: no budget/block/challenge degradation markers
    for bad in ("[BUDGET_HIT]", "[PARTIAL_REPORT]", "[BLOCK_DETECTED]", "[ATHOME_CHALLENGE]"):
        assert not any(msg.startswith(bad) for msg in messages), f"unexpected marker {bad}"
    # 3) forbidden failure patterns never appear
    full_log = "\n".join(messages)
    for pat in FORBIDDEN_PATTERNS:
        assert pat not in full_log, f"forbidden failure pattern present: {pat}"

    # 4) report files exist (md + json) and parse
    md_files = sorted((tmp_path / "reports").glob("report-*.md"))
    json_files = sorted((tmp_path / "reports").glob("report-*.json"))
    assert md_files and json_files
    for f in md_files:
        assert f.read_text(encoding="utf-8").strip()
    for f in json_files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert isinstance(payload["recommendations"], list)
        assert len(payload["recommendations"]) >= 1, (
            f"expected >= 1 recommendation in {f.name}, got {len(payload['recommendations'])}"
        )

    # 5) the store retained the search history and harvest
    assert store.search_history(limit=10)
    assert len(store.seen_internal_ids()) == len(parse_list_page(SMALL_LIST_HTML))
