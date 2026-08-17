"""Versioned filter map schema and validation (milestone M2, T10).

This module owns the contract between the extracted filter snapshot
``filters/data/filter_map.v1.json`` and the runtime encoder. It reuses the
:class:`FilterMap` / :class:`FilterOption` pydantic models from
``athome_harness.models`` so there is exactly one canonical shape.

The schema tracks, per flow and field, the "conditions map" metadata from
SPEC.md section 1.1: cardinality (``single``, ``multi``, ``range``, ``bool``),
the expected code prefix (or ``None`` for code-less fields such as numeric
SORT/ITEMNUM), optional field aliases, the HTML control family used at
extraction time (``select`` or ``checkbox``), and whether a field's labels must
be monotonically ordered by magnitude. Validation rejects unknown schema
versions, unknown flows and filters, duplicate codes, empty labels, codes that
violate a field's prefix rule, and non-monotonic price/area/age lineages.

Flow context matters: the same filter name maps to different code prefixes per
flow (rent prices are ``kc``, buy prices are ``kp``; area is ``kt1xx`` on rent
and ``kt1xx``/``kt4xx`` on buy). Every lookup and every validation step
therefore carries ``(flow, field)`` so codes never collide across flow/field
contexts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from athome_harness.models import FilterMap, FilterOption

# The only schema version the harness understands. The encoder and the dump
# tool both refuse maps with any other version (SPEC.md section 2).
SUPPORTED_SCHEMA_VERSION = 1

# Valid cardinalities, matching SPEC.md section 1.1 exactly.
Cardinality = Literal["single", "multi", "range", "bool"]

# HTML control family used by the dump tool to extract a field's options.
ControlKind = Literal["select", "checkbox"]

# The two flows the harness supports.
FLOWS: frozenset[str] = frozenset({"rent", "buy"})


@dataclass(frozen=True)
class FieldCondition:
    """Static per-field metadata from the conditions map (SPEC 1.1).

    ``cardinality`` controls how the encoder builds POST params. ``control``
    tells the dump tool how to find options (``<select>`` vs checkbox group).
    ``code_regex`` validates option codes; None means the options carry no code
    prefix (e.g. numeric ``SORT``/``ITEMNUM`` codes, boolean toggles).
    ``html_base`` is the DOM base name of the field: server-side HTML renders
    checkbox groups as ``FIELD[]``; ``pair`` names the two real HTML fields a
    ``range`` logical field maps to. ``aliases`` are accepted alternate names
    for the canonical field in a plan. ``monotonic`` marks magnitude-ordered
    lineages (price, area, age) whose labels must never decrease.
    """

    cardinality: Cardinality
    control: ControlKind
    code_regex: re.Pattern[str] | None
    html_base: str
    pair: tuple[str, str] | None = None
    aliases: tuple[str, ...] = ()
    monotonic: bool = False


def _condition(
    cardinality: Cardinality,
    control: ControlKind,
    pattern: re.Pattern[str] | str | None,
    html_base: str,
    *,
    pair: tuple[str, str] | None = None,
    aliases: tuple[str, ...] = (),
    monotonic: bool = False,
) -> FieldCondition:
    """Build a :class:`FieldCondition`, compiling a raw regex if given."""
    regex = pattern if isinstance(pattern, re.Pattern) else (
        re.compile(pattern) if pattern else None
    )
    return FieldCondition(
        cardinality=cardinality,
        control=control,
        code_regex=regex,
        html_base=html_base,
        pair=pair,
        aliases=aliases,
        monotonic=monotonic,
    )


# The canonical conditions map: flow -> field -> metadata. Field presence,
# prefixes and option counts were VERIFIED against live dumps of the rent
# (chintai/osaka/list, 2026-07-08) and buy (mansion/tokyo/list) list pages.
CONDITIONS: dict[str, dict[str, FieldCondition]] = {
    "rent": {
        # Selects (single value), mirroring SPEC.md section 1.1.
        "PRICEFROM": _condition("single", "select", r"^kc\d+$", "PRICEFROM", monotonic=True),
        "PRICETO": _condition("single", "select", r"^kc\d+$", "PRICETO", monotonic=True),
        "MENSEKI": _condition("single", "select", r"^kt\d+$", "MENSEKI", monotonic=True),
        "EKITOHO": _condition("single", "select", r"^ke\d+$", "EKITOHO", monotonic=True),
        "CHIKUNENSU": _condition("single", "select", r"^kn\d+$", "CHIKUNENSU", monotonic=True),
        "KEIYAKU": _condition("single", "select", r"^ki\d+$", "KEIYAKU"),
        "SORT": _condition("single", "select", None, "SORT"),
        "TATEMONONUM": _condition("single", "select", None, "TATEMONONUM"),
        "PRICE": _condition(
            "range", "select", None, "PRICE",
            pair=("PRICEFROM", "PRICETO"),
            aliases=("PRICE_RANGE", "rent price"),
        ),
        # Checkbox groups (multi value).
        "MADORI": _condition("multi", "checkbox", r"^km\d+$", "MADORI"),
        "PRICEOPT": _condition("multi", "checkbox", r"^kc2\d\d$", "PRICEOPT"),
        "SHUMOKU": _condition("multi", "checkbox", r"^kb\d+$", "SHUMOKU"),
        "TATEKOUZOU": _condition("multi", "checkbox", r"^kh\d+$", "TATEKOUZOU"),
        "SYUHENKANKYO": _condition("multi", "checkbox", r"^kw\d+$", "SYUHENKANKYO"),
        "GAZO": _condition("multi", "checkbox", r"^kg\d+$", "GAZO"),
        "KODAWARI": _condition("multi", "checkbox", r"^[A-Za-z]{1,3}\d*$", "KODAWARI"),
        # Boolean toggles (single option each on the rent page).
        "APPEAL": _condition("bool", "checkbox", r"^ka\d+$", "APPEAL"),
        "RENOVATION": _condition("bool", "checkbox", r"^ak\d+$", "REFORM"),
    },
    "buy": {
        "PRICEFROM": _condition("single", "select", r"^kp\d+$", "PRICEFROM", monotonic=True),
        "PRICETO": _condition("single", "select", r"^kp\d+$", "PRICETO", monotonic=True),
        "MENSEKI": _condition("single", "select", r"^kt\d+$", "MENSEKI", monotonic=True),
        "MENSEKITO": _condition("single", "select", r"^kt4\d+$", "MENSEKITO", monotonic=True),
        "EKITOHO": _condition("single", "select", r"^ke\d+$", "EKITOHO", monotonic=True),
        "CHIKUNENSU": _condition("single", "select", r"^kn\d+$", "CHIKUNENSU", monotonic=True),
        "SORT": _condition("single", "select", None, "SORT"),
        "itemus": _condition("single", "select", None, "itemus"),
        "PRICE": _condition(
            "range", "select", None, "PRICE",
            pair=("PRICEFROM", "PRICETO"),
            aliases=("PRICE_RANGE", "buy price"),
        ),
        "MADORI": _condition("multi", "checkbox", r"^km\d+$", "MADORI"),
        "GAZO": _condition("multi", "checkbox", r"^kg\d+$", "GAZO"),
        "KKGROUP": _condition("multi", "checkbox", r"^kk\d+$", "KKGROUP"),
    },
}

# Filters that must be present in every valid snapshot per flow. A missing one
# is a hard schema failure, not a silent omission.
REQUIRED_FILTERS: dict[str, frozenset[str]] = {
    "rent": frozenset(
        {
            "PRICEFROM", "PRICETO", "MADORI", "MENSEKI", "EKITOHO", "CHIKUNENSU",
            "KEIYAKU", "SHUMOKU", "TATEKOUZOU", "SYUHENKANKYO", "KODAWARI",
        }
    ),
    "buy": frozenset(
        {"PRICEFROM", "PRICETO", "MADORI", "MENSEKI", "EKITOHO", "CHIKUNENSU"}
    ),
}


class FilterMapError(ValueError):
    """Base error for filter map schema problems."""


class UnsupportedSchemaVersionError(FilterMapError):
    """Raised when a FilterMap carries an unknown ``version``.

    The harness refuses maps whose schema version it does not understand
    (SPEC.md section 2). The marker contract lists ``FILTER_MAP_SCHEMA_UNSUPPORTED``
    as a failure pattern, so callers must handle this typed error and must
    never emit that marker on a valid run.
    """


class FilterMapSchemaViolation(FilterMapError):
    """Raised when a snapshot violates a rule: missing, duplicate, prefix, empty, monotonic."""


def validate(filter_map: FilterMap) -> FilterMap:
    """Validate a :class:`FilterMap` and return it when every rule passes.

    Rules enforced:
      * schema version must equal :data:`SUPPORTED_SCHEMA_VERSION`;
      * ``mappings`` must contain every flow in :data:`FLOWS` (a map that
        silently drops the rent or buy flow is invalid);
      * every flow must carry every filter named by :data:`REQUIRED_FILTERS`;
      * every field must be defined in :data:`CONDITIONS`;
      * option codes must match the field's regex, be unique and have a
        non-empty label;
      * monotonic fields (price, area, age) must keep their labels in the same
        numeric order as the codes.

    Raises :class:`UnsupportedSchemaVersionError` for a version mismatch and
    :class:`FilterMapSchemaViolation` on any other rule break.
    """
    if not isinstance(filter_map, FilterMap):
        raise FilterMapSchemaViolation("filter map must be a FilterMap instance")
    if filter_map.version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"unsupported filter map schema version {filter_map.version}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}"
        )
    missing_flows = FLOWS - set(filter_map.mappings)
    if missing_flows:
        raise FilterMapSchemaViolation(
            f"filter map missing flows: {', '.join(sorted(missing_flows))}"
        )
    for flow, fields in filter_map.mappings.items():
        _validate_flow(flow, fields)
    return filter_map


def _validate_flow(flow: str, fields: dict[str, list[FilterOption]]) -> None:
    """Validate one flow's field dict against the conditions map."""
    known = CONDITIONS.get(flow)
    if known is None:
        raise FilterMapSchemaViolation(f"unknown flow '{flow}' in filter map")
    missing = REQUIRED_FILTERS.get(flow, frozenset()) - set(fields)
    if missing:
        raise FilterMapSchemaViolation(
            f"flow '{flow}' missing required filters: {', '.join(sorted(missing))}"
        )
    for field_name, options in fields.items():
        if field_name not in known:
            raise FilterMapSchemaViolation(f"unknown filter '{field_name}' for flow '{flow}'")
        condition = known[field_name]
        seen: set[str] = set()
        for index, option in enumerate(options):
            _validate_option(flow, field_name, option, condition, index, seen)
        if condition.monotonic:
            _check_monotonic(flow, field_name, options)


def _validate_option(
    flow: str,
    field_name: str,
    option: FilterOption,
    condition: FieldCondition,
    index: int,
    seen: set[str],
) -> None:
    """Enforce prefix, code uniqueness and non-empty label for one option."""
    code = option.code
    if not code or not code.strip():
        raise FilterMapSchemaViolation(f"{flow}.{field_name}[{index}]: empty option code")
    if condition.code_regex is not None and not condition.code_regex.match(code):
        raise FilterMapSchemaViolation(
            f"{flow}.{field_name}[{index}]: code '{code}' does not match "
            f"pattern '{condition.code_regex.pattern}'"
        )
    if not option.label or not option.label.strip():
        raise FilterMapSchemaViolation(
            f"{flow}.{field_name}[{index}]: empty label for code '{code}'"
        )
    if code in seen:
        raise FilterMapSchemaViolation(f"{flow}.{field_name}: duplicate code '{code}'")
    seen.add(code)


def _check_monotonic(flow: str, field_name: str, options: list[FilterOption]) -> None:
    """Ensure price/area-like labels are numerically non-decreasing along the list.

    Placeholder labels such as "No lower limit" or "No upper limit" carry no
    numeric magnitude and are skipped; the remaining magnitudes must strictly
    increase. Supports the Japanese unit suffixes (``万``/``億`` and ``m²``) and
    the English "thousand/million" forms present in the live dumps.
    """
    previous: int | None = None
    for option in options:
        magnitude = _label_magnitude(option.label)
        if magnitude is None:
            continue
        if previous is not None and magnitude <= previous:
            raise FilterMapSchemaViolation(
                f"{flow}.{field_name}: non-monotonic order at code '{option.code}' "
                f"({magnitude} after {previous})"
            )
        previous = magnitude


def _label_magnitude(label: str) -> int | None:
    """Parse the numeric magnitude from a price/area/age label, or None.

    Handles Japanese ``万`` (10,000) and ``億`` (100,000,000), the ``m``/``m²``
    area suffix, plain comma-separated integers, and English "N million"
    phrases. Returns None for placeholders like "No lower limit" or "New
    construction" that carry no inferable magnitude.
    """
    text = label.strip()
    if not text:
        return None
    lower = text.lower()
    if any(word in lower for word in ("no lower", "no upper", "no specification")):
        return None
    if "新築" in text or "new construction" in lower:
        # Newest possible; treat as magnitude 0 so it sorts before any age.
        return 0
    if "億" in text or "万" in text:
        # Japanese concatenated magnitudes, e.g. "3億5,000万円". Each unit
        # segment contributes its value; bare trailing numbers are 万-era.
        # Decimals appear in live dumps (e.g. "2.5万円"), so each segment is
        # captured with an optional fractional part.
        total = 0.0
        for number_str, unit in re.findall(r"(\d[\d,]*\.?\d*)\s*([億万])?", text):
            number = float(number_str.replace(",", ""))
            if unit == "億":
                total += number * 100_000_000
            elif unit == "万":
                total += number * 10_000
            else:
                total += number
        return int(total)
    if "million" in lower:
        milli = re.search(r"([\d,]+)\s*million", lower)
        return int(milli.group(1).replace(",", "")) * 1_000_000 if milli else None
    if "thousand" in lower:
        thous = re.search(r"([\d,]+)\s*thousand", lower)
        return int(thous.group(1).replace(",", "")) * 1_000 if thous else None
    m_match = re.search(r"(\d[\d,]*)m", lower)
    if m_match:
        return int(m_match.group(1).replace(",", ""))
    # Bare comma-separated number (e.g. "320,000 yen", "20m² or more"). The
    # Japanese yen/unit cases were already handled above.
    number_match = re.search(r"([\d,]+)", text)
    if not number_match:
        return None
    return int(number_match.group(1).replace(",", ""))


def canonical_payload(version: int, mappings: dict[str, dict[str, list[FilterOption]]]) -> str:
    """Serialize ``version`` and ``mappings`` into a deterministic JSON payload.

    The dump tool hashes this payload to produce ``content_hash``; the encoder
    and tests recompute it the same way so a stored map always validates against
    its own hash. Keys are sorted to keep the output stable across Python
    versions.
    """
    payload = {
        "version": version,
        "mappings": {
            flow: {
                field: [{"code": o.code, "label": o.label} for o in options]
                for field, options in fields.items()
            }
            for flow, fields in mappings.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_content_hash(mappings: dict[str, dict[str, list[FilterOption]]]) -> str:
    """Return the truncated SHA-256 fingerprint of the canonical payload.

    The hash is truncated to 12 hex chars to match the ``[FILTERMAP_OK]``
    marker contract (``hash=<sha256[:12]>``).
    """
    payload = canonical_payload(SUPPORTED_SCHEMA_VERSION, mappings)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
