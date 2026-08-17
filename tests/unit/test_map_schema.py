"""Unit tests for the versioned filter map schema and validation (M2, T10).

Tests run against a deterministic fixture page tree, never the live network.
Valid maps are built by extracting the fixture HTML; invalid maps are produced
by mutating a valid map, one rule at a time, so every failure path is covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athome_harness.filters.map_schema import (
    CONDITIONS,
    FLOWS,
    REQUIRED_FILTERS,
    SUPPORTED_SCHEMA_VERSION,
    FilterMapSchemaViolation,
    UnsupportedSchemaVersionError,
    canonical_payload,
    compute_content_hash,
    validate,
)
from athome_harness.models import FilterMap

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_map() -> FilterMap:
    """Build the validated filter map from the deterministic fixture pages."""
    from tools.dump_filter_map import extract_flow

    rent = extract_flow(
        (FIXTURES / "filter_map_rent.html").read_text(encoding="utf-8"), "rent"
    )
    buy = extract_flow(
        (FIXTURES / "filter_map_buy.html").read_text(encoding="utf-8"), "buy"
    )
    filter_map = FilterMap(
        version=SUPPORTED_SCHEMA_VERSION,
        content_hash=compute_content_hash({"rent": rent, "buy": buy}),
        mappings={"rent": rent, "buy": buy},
    )
    return validate(filter_map)


def deep_copy_map(source: FilterMap) -> FilterMap:
    """Return an independent FilterMap with the same data as ``source``.

    ``model_copy(deep=True)`` re-runs pydantic validation, which is what the
    mutation tests need: each test builds a valid copy and introduces exactly
    one violation.
    """
    return FilterMap.model_validate(source.model_dump())


# --------------------------------------------------------------------------
# Valid maps
# --------------------------------------------------------------------------


def test_valid_fixture_passes_validation() -> None:
    """A map extracted from the fixture pages must validate cleanly."""
    filter_map = fixture_map()
    assert validate(filter_map) is filter_map
    assert filter_map.version == SUPPORTED_SCHEMA_VERSION
    assert set(filter_map.mappings) == {"rent", "buy"}


def test_required_filters_present_in_fixture() -> None:
    """Every filter REQUIRED_FILTERS names must survive extraction."""
    filter_map = fixture_map()
    for flow in FLOWS:
        assert REQUIRED_FILTERS[flow] <= set(filter_map.mappings[flow])


def test_conditions_cover_required_filters() -> None:
    """Every required filter must be defined in the conditions map."""
    for flow, required in REQUIRED_FILTERS.items():
        assert required <= set(CONDITIONS[flow])


def test_canonical_payload_is_stable_and_deterministic() -> None:
    """Hashing the same map twice gives the same truncated SHA-256."""
    filter_map = fixture_map()
    first = canonical_payload(filter_map.version, filter_map.mappings)
    second = canonical_payload(filter_map.version, filter_map.mappings)
    assert first == second
    assert compute_content_hash(filter_map.mappings) == compute_content_hash(
        filter_map.mappings
    )
    # The hash is the standard 12-hex-char truncation of SHA-256.
    assert len(compute_content_hash(filter_map.mappings)) == 12
    assert all(c in "0123456789abcdef" for c in compute_content_hash(filter_map.mappings))


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

def test_unsupported_version_is_rejected() -> None:
    """A map with a version we do not understand must be refused."""
    filter_map = fixture_map()
    bad = FilterMap.model_validate(filter_map.model_dump())
    bad.version = SUPPORTED_SCHEMA_VERSION + 1
    with pytest.raises(UnsupportedSchemaVersionError):
        validate(bad)


def test_validate_rejects_non_filtermap() -> None:
    """Passing a plain dict (not a FilterMap) must raise a schema violation."""
    with pytest.raises(FilterMapSchemaViolation):
        validate({"version": SUPPORTED_SCHEMA_VERSION, "mappings": {}})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Flow and filter presence
# ---------------------------------------------------------------------------

def test_unknown_flow_is_rejected() -> None:
    """A flow name outside rent/buy must be a schema violation."""
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    invalid.mappings["sell"] = invalid.mappings["rent"]
    with pytest.raises(FilterMapSchemaViolation, match="unknown flow"):
        validate(invalid)


def test_missing_required_filter_is_rejected() -> None:
    """Removing a filter REQUIRED_FILTERS names must fail validation."""
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    del invalid.mappings["rent"]["PRICEFROM"]
    with pytest.raises(FilterMapSchemaViolation, match="missing required filters"):
        validate(invalid)


def test_unknown_filter_name_is_rejected() -> None:
    """A field not present in the conditions map must be rejected."""
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    invalid.mappings["rent"]["NOT_A_FILTER"] = invalid.mappings["rent"]["SORT"]
    with pytest.raises(FilterMapSchemaViolation, match="unknown filter"):
        validate(invalid)


# ---------------------------------------------------------------------------
# Option-level rules
# ---------------------------------------------------------------------------

def test_empty_option_code_rejected() -> None:
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    invalid.mappings["rent"]["PRICEFROM"][0].code = "  "
    with pytest.raises(FilterMapSchemaViolation, match="empty option code"):
        validate(invalid)


def test_code_prefix_violation_rejected() -> None:
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    invalid.mappings["rent"]["MADORI"][0].code = "zz999"
    with pytest.raises(FilterMapSchemaViolation, match="does not match pattern"):
        validate(invalid)


def test_empty_label_rejected() -> None:
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    invalid.mappings["buy"]["MENSEKI"][1].label = "   "
    with pytest.raises(FilterMapSchemaViolation, match="empty label"):
        validate(invalid)


def test_duplicate_code_rejected() -> None:
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    options = invalid.mappings["rent"]["MADORI"]
    options[1].code = options[0].code
    with pytest.raises(FilterMapSchemaViolation, match="duplicate code"):
        validate(invalid)


def test_monotonic_price_violation_rejected() -> None:
    """A price lineage that goes down must be rejected as non-monotonic."""
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    # Swap two adjacent rental price bounds so the magnitude decreases.
    options = invalid.mappings["rent"]["PRICEFROM"]
    options[1], options[2] = options[2], options[1]
    with pytest.raises(FilterMapSchemaViolation, match="non-monotonic"):
        validate(invalid)


def test_monotonic_area_violation_rejected() -> None:
    """Buy MENSEKI has monotonic=True, so a reversal must also fail."""
    filter_map = fixture_map()
    invalid = deep_copy_map(filter_map)
    options = invalid.mappings["buy"]["MENSEKI"]
    options[1], options[2] = options[2], options[1]
    with pytest.raises(FilterMapSchemaViolation, match="non-monotonic"):
        validate(invalid)


def test_short_hash_length_matches_marker_contract() -> None:
    """The content hash is exactly the 12-char prefix the markers print."""
    filter_map = fixture_map()
    assert len(filter_map.content_hash) == 12
