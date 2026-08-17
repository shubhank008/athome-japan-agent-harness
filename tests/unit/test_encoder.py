"""Unit tests for the filter map encoder (M2, T13).

Exercises every cardinality (single, multi, range, bool), the aliases
metadata, unknown filter/value hard failures, flow-context collisions, and the
two documented range/bool list encodings in SearchPlan.hard_filters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athome_harness.filters.encoder import UnknownFilter, UnknownFilterValue, encode_plan
from athome_harness.filters.map_schema import SUPPORTED_SCHEMA_VERSION, validate
from athome_harness.models import FilterMap, SearchPlan

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _build_map() -> FilterMap:
    """Build the validated filter map from the deterministic fixtures."""
    from tools.dump_filter_map import extract_flow

    rent = extract_flow(
        (FIXTURES / "filter_map_rent.html").read_text(encoding="utf-8"), "rent"
    )
    buy = extract_flow(
        (FIXTURES / "filter_map_buy.html").read_text(encoding="utf-8"), "buy"
    )
    filter_map = FilterMap(
        version=SUPPORTED_SCHEMA_VERSION,
        content_hash="0" * 12,
        mappings={"rent": rent, "buy": buy},
    )
    return validate(filter_map)


def _plan(flow: str = "rent", hard_filters: dict[str, list[str]] | None = None) -> SearchPlan:
    """Build a minimal SearchPlan with only the fields the encoder touches."""
    return SearchPlan(
        flow=flow,  # type: ignore[arg-type]
        prefecture="osaka",
        hard_filters=hard_filters or {},
    )


def _params(plan: SearchPlan) -> dict[str, list[str]]:
    """Group the encoded pair list into a ``name -> [values]`` dict."""
    grouped: dict[str, list[str]] = {}
    for name, value in encode_plan(plan, _build_map()):
        grouped.setdefault(name, []).append(value)
    return grouped


# --------------------------------------------------------------------------
# Cardinalities
# --------------------------------------------------------------------------

def test_single_select_encodes_one_pair() -> None:
    """single: FIELD=<code>."""
    plan = _plan("rent", {"PRICEFROM": ["kc041"]})
    assert encode_plan(plan, _build_map()) == [("PRICEFROM", "kc041")]


def test_multi_madori_repeats_field_with_suffix() -> None:
    """multi: repeated FIELD[]=<code> for each selection."""
    plan = _plan("rent", {"MADORI": ["km002", "km004"]})
    params = _params(plan)
    assert params["MADORI[]"] == ["km002", "km004"]
    # No other keys may leak in.
    assert set(params) == {"MADORI[]"}


def test_range_price_emits_from_to_pair() -> None:
    """range: PRICEFROM/PRICETO pair of codes in order."""
    plan = _plan("rent", {"PRICE": ["kc041", "kc099"]})
    assert encode_plan(plan, _build_map()) == [
        ("PRICEFROM", "kc041"),
        ("PRICETO", "kc099"),
    ]


def test_range_alias_name_resolves_to_canonical() -> None:
    """The PRICE_RANGE alias must act exactly like PRICE."""
    plan = _plan("rent", {"PRICE_RANGE": ["kc041", "kc099"]})
    assert encode_plan(plan, _build_map()) == [
        ("PRICEFROM", "kc041"),
        ("PRICETO", "kc099"),
    ]


def test_bool_enabled_emits_toggle_code() -> None:
    """bool on: emit the single option code of the field."""
    plan = _plan("rent", {"APPEAL": ["true"]})
    assert encode_plan(plan, _build_map()) == [("APPEAL[]", "ka001")]


def test_bool_disabled_emits_nothing() -> None:
    """bool off: no parameter at all."""
    plan = _plan("rent", {"APPEAL": ["false"]})
    assert encode_plan(plan, _build_map()) == []


@pytest.mark.parametrize("word", ["true", "TRUE", "1", "yes", "on"])
def test_bool_true_spellings_accepted(word: str) -> None:
    plan = _plan("rent", {"APPEAL": [word]})
    assert encode_plan(plan, _build_map()) == [("APPEAL[]", "ka001")]


@pytest.mark.parametrize("word", ["false", "FALSE", "0", "no", "off"])
def test_bool_false_spellings_emit_nothing(word: str) -> None:
    plan = _plan("rent", {"APPEAL": [word]})
    assert encode_plan(plan, _build_map()) == []


# ---------------------------------------------------------------------------
# Unknown filters and values must raise, never guess
# ---------------------------------------------------------------------------

def test_unknown_filter_raises() -> None:
    with pytest.raises(UnknownFilter, match="no filter named"):
        encode_plan(_plan("rent", {"BOGUS": ["x"]}), _build_map())


def test_unknown_value_raises() -> None:
    with pytest.raises(UnknownFilterValue, match="unknown code"):
        encode_plan(_plan("rent", {"MADORI": ["zz999"]}), _build_map())


def test_single_with_wrong_arity_raises() -> None:
    with pytest.raises(UnknownFilterValue, match="expected exactly one"):
        encode_plan(_plan("rent", {"PRICEFROM": ["kc041", "kc042"]}), _build_map())


def test_range_with_wrong_arity_raises() -> None:
    with pytest.raises(UnknownFilterValue, match="exactly two"):
        encode_plan(_plan("rent", {"PRICE": ["kc041"]}), _build_map())


def test_bool_unparseable_raises() -> None:
    with pytest.raises(UnknownFilterValue, match="bool"):
        encode_plan(_plan("rent", {"APPEAL": ["maybe"]}), _build_map())


def test_no_unknown_marker_emitted(caplog) -> None:
    """On success the encoder must log params= and unmapped=0, no UNKNOWN marker."""
    import logging

    with caplog.at_level(logging.INFO):
        encode_plan(_plan("rent", {"MADORI": ["km002"]}), _build_map())
    assert any("UNKNOWN_FILTER_ENCODED" not in rec.getMessage() for rec in caplog.records)
    assert any("[FILTER_ENCODE] params=1 unmapped=0" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Flow-context collisions
# ---------------------------------------------------------------------------

def test_same_code_encodes_per_flow() -> None:
    """The same code 'km002' in rent and buy must map in its own flow's map."""
    rent = _params(_plan("rent", {"MADORI": ["km002"]}))
    buy = _params(_plan("buy", {"MADORI": ["km017"]}))
    assert rent["MADORI[]"] == ["km002"]
    assert buy["MADORI[]"] == ["km017"]


def test_buy_price_uses_buy_prefix() -> None:
    """A buy plan's price encodes with the kp prefix, not the rent kc prefix."""
    plan = _params(_plan("buy", {"PRICE": ["kp001", "kp003"]}))
    assert plan["PRICEFROM"] == ["kp001"]
    assert plan["PRICETO"] == ["kp003"]


def test_filter_name_only_valid_in_one_flow_raises_in_other() -> None:
    """KKGROUP is buy-only; referencing it from a rent plan must fail."""
    with pytest.raises(UnknownFilter):
        encode_plan(_plan("rent", {"KKGROUP": ["kk002"]}), _build_map())
