"""Unit tests for the natural-language query parser (M4, T19).

Uses a canned :class:`SequenceProvider` so no network is involved. Covers the
rent-vs-buy split, filter-map summary loading, hard filter resolution,
unmappable-to-soft-preference demotion, and ambiguity routing.
"""

from __future__ import annotations

import pytest

from athome_harness.llm.query_parser import (
    ClarificationNeeded,
    QueryParser,
    build_filter_map_summary,
)
from tests.unit._fakes import SequenceProvider, build_filter_map


def _rent_plan_json() -> str:
    return (
        '{"flow": "rent", "prefecture": "osaka", "cities": [], '
        '"ambiguous": false, "clarification_question": null, '
        '"hard_filters": {"MADORI": ["1K", "1DK"], "EKITOHO": ["10 min or less"]}, '
        '"soft_prefs": ["real kitchen"]}'
    )


def test_rent_flow_resolves_hard_filters_to_codes() -> None:
    """Rent intent labels resolve to the rent code space."""
    provider = SequenceProvider(['{"flow": "rent"}', _rent_plan_json()])
    parser = QueryParser(provider, build_filter_map())
    plan = parser.parse("pet-friendly 1K or 1DK in osaka")
    assert plan.flow == "rent"
    assert plan.prefecture == "osaka"
    assert plan.hard_filters["MADORI"] == ["km003", "km004"]
    assert plan.hard_filters["EKITOHO"] == ["ke003"]
    assert plan.soft_prefs == ["real kitchen"]


def test_detected_flow_controls_resolution_when_model_changes_flow() -> None:
    """The filter map must match the flow used for the initial prompt."""
    provider = SequenceProvider(
        [
            '{"flow": "rent"}',
            (
                '{"flow": "buy", "prefecture": "osaka", "cities": [], '
                '"hard_filters": {"MADORI": ["1K"]}, "soft_prefs": []}'
            ),
        ]
    )
    plan = QueryParser(provider, build_filter_map()).parse("rent a 1K in osaka")
    assert plan.flow == "rent"
    assert plan.hard_filters["MADORI"] == ["km003"]


def test_buy_flow_uses_buy_code_space() -> None:
    """Buy intent uses the buy flow's mapping (price prefix differs)."""
    provider = SequenceProvider(
        [
            '{"flow": "buy"}',
            (
                '{"flow": "buy", "prefecture": "tokyo", "cities": ["minato"], '
                '"ambiguous": false, "clarification_question": null, '
                '"hard_filters": {"MADORI": ["4LDK以上"]}, "soft_prefs": []}'
            ),
        ]
    )
    parser = QueryParser(provider, build_filter_map())
    plan = parser.parse("a 4LDK+ apartment in minato, tokyo")
    assert plan.flow == "buy"
    assert plan.cities == ["minato"]
    # The buy MADORI code space shares km prefixes; assert label resolution.
    assert plan.hard_filters["MADORI"] == ["km017"]


def test_unmappable_label_demoted_to_soft_pref() -> None:
    """A label with no matching option becomes a soft pref, never dropped."""
    provider = SequenceProvider(
        [
            '{"flow": "rent"}',
            (
                '{"flow": "rent", "prefecture": "osaka", "cities": [], '
                '"ambiguous": false, "clarification_question": null, '
                '"hard_filters": {"MADORI": ["1K", "not-a-real-option"], '
                '"NOTAFILTER": ["who knows"]}, "soft_prefs": []}'
            ),
        ]
    )
    parser = QueryParser(provider, build_filter_map())
    plan = parser.parse("1K near a station")
    assert plan.hard_filters["MADORI"] == ["km003"]
    assert "MADORI: not-a-real-option" in plan.soft_prefs
    assert "NOTAFILTER: who knows" in plan.soft_prefs


def test_ambiguous_query_raises_clarification() -> None:
    """Ambiguity routes to ClarificationNeeded with the model's question."""
    provider = SequenceProvider(
        [
            '{"flow": "rent"}',
            (
                '{"flow": "rent", "prefecture": "osaka", "cities": [], '
                '"ambiguous": true, '
                '"clarification_question": "Which ward do you prefer?", '
                '"hard_filters": {}, "soft_prefs": []}'
            ),
        ]
    )
    parser = QueryParser(provider, build_filter_map())
    with pytest.raises(ClarificationNeeded) as exc_info:
        parser.parse("something near osaka")
    assert exc_info.value.question == "Which ward do you prefer?"


def test_filter_map_summary_includes_labels_and_codes() -> None:
    """The prompt summary lists option labels and codes for the chosen flow."""
    summary = build_filter_map_summary(build_filter_map(), "rent")
    assert "MADORI" in summary
    assert "1K" in summary
    assert "km003" in summary
