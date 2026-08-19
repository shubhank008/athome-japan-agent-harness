"""Filter map encoder (milestone M2, T13).

Turns a validated SearchPlan plus the checked-in FilterMap
into the POST parameter pairs the harvester sends to AtHome. The encoder is
deliberately dumb: it never guesses. Every filter and every selected code must
resolve against the map for the plan's flow, otherwise it raises
UnknownFilter or UnknownFilterValue instead of silently encoding an
``UNKNOWN_FILTER_ENCODED`` marker.

The cardinality contract comes from SPEC.md section 1.1 and is read off the
:data:`CONDITIONS` metadata in ``map_schema.py``:

  * ``single`` -> one ``FIELD=<code>`` parameter;
  * ``multi``  -> repeated ``FIELD[]=<code>`` parameters;
  * ``range``  -> a FROM/TO pair, e.g. ``PRICEFROM=<from>`` + ``PRICETO=<to>``;
  * ``bool``   -> a toggle. True (or `true`/`1`/`yes`/`on`) emits the field's
    single option code; false omits the parameter entirely, matching the
    server form which only submits checked checkboxes.

Range and bool values in ``SearchPlan.hard_filters`` are stored as
``list[str]`` to stay compatible with the model's ``dict[str, list[str]]``
shape:

  * ``range``: exactly two codes ``[FROM, TO]`` in submission order.
  * ``bool``: ``["true"]``/``["false"]`` (case-insensitive) or
    ``["1"]``/``["0"]``.

Both representations are documented here and exercised by the unit tests, so
the query parser (M4) and the encoder agree without mutating SearchPlan.
"""

from __future__ import annotations

import logging

from athome_harness.filters.map_schema import (
    CONDITIONS,
    SUPPORTED_SCHEMA_VERSION,
    FieldCondition,
    UnsupportedSchemaVersionError,
)
from athome_harness.models import FilterMap, FilterOption, SearchPlan

logger = logging.getLogger(__name__)

# Accepted boolean toggle encodings for ``bool`` filters.
_TRUE_WORDS: frozenset[str] = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS: frozenset[str] = frozenset({"false", "0", "no", "off"})


class UnknownFilter(ValueError):
    """Raised when a plan references a filter the map cannot encode.

    A hard error by design: the encoder never drops or guesses a filter,
    because doing so would silently change search semantics (SPEC section 2).
    """


class UnknownFilterValue(ValueError):
    """Raised when a plan selects a code absent from the map for a filter.

    Distinguishes a wrong code from a wrong name so callers can report which
    part of the plan was unmappable.
    """


def encode_plan(plan: SearchPlan, filter_map: FilterMap) -> list[tuple[str, str]]:
    """Encode ``plan.hard_filters`` into ordered ``(name, value)`` POST pairs.

    The map must already pass ``map_schema.validate``; the encoder re-checks
    the schema version defensively and refuses a version it cannot trust
    (SPEC section 2). Returns a list that preserves repeated ``FIELD[]=<code>``
    keys so callers can build a faithful ``application/x-www-form-urlencoded``
    body.

    Raises :class:`UnknownFilter` or :class:`UnknownFilterValue` when anything
    is unmappable, and logs the ``[FILTER_ENCODE] params=<n> unmapped=0``
    contract marker only on success.
    """
    if filter_map.version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"unsupported filter map schema version {filter_map.version}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}"
        )
    params: list[tuple[str, str]] = []
    for filter_name, values in plan.hard_filters.items():
        params.extend(_encode_field(plan.flow, filter_name, values, filter_map))
    logger.info("[FILTER_ENCODE] params=%d unmapped=0", len(params))
    return params


def _encode_field(
    flow: str,
    filter_name: str,
    values: list[str],
    filter_map: FilterMap,
) -> list[tuple[str, str]]:
    """Resolve a plan filter against the flow's conditions and map.

    ``filter_name`` may be the canonical name or one of its aliases. The
    code space is always taken from the *plan's flow*, so identical names in
    different flows never collide.
    """
    canonical, condition = _lookup_condition(flow, filter_name)
    if condition.cardinality == "single":
        return _encode_single(flow, canonical, values, filter_map, condition)
    if condition.cardinality == "multi":
        return _encode_multi(flow, canonical, values, filter_map, condition)
    if condition.cardinality == "range":
        if condition.pair is None:
            raise UnknownFilter(f"{flow}.{filter_name}: range condition has no pair")
        return _encode_range(flow, condition.pair, values, filter_map)
    if condition.cardinality == "bool":
        return _encode_bool(flow, canonical, values, filter_map, condition)
    raise UnknownFilter(
        f"{flow}.{filter_name}: cardinality '{condition.cardinality}' is not encodable"
    )


def _lookup_condition(flow: str, filter_name: str) -> tuple[str, FieldCondition]:
    """Return ``(canonical_name, condition)`` for ``filter_name`` in ``flow``.

    Matches either the canonical field name or any of its aliases, and raises
    :class:`UnknownFilter` when nothing in the flow matches.
    """
    conditions = CONDITIONS.get(flow)
    if conditions is None:
        raise UnknownFilter(f"unknown flow '{flow}'")
    for canonical, condition in conditions.items():
        if filter_name == canonical or filter_name in condition.aliases:
            return canonical, condition
    raise UnknownFilter(f"{flow}: no filter named '{filter_name}' in conditions map")


def _encode_single(
    flow: str,
    field_name: str,
    values: list[str],
    filter_map: FilterMap,
    condition: FieldCondition,
) -> list[tuple[str, str]]:
    """Encode a ``single`` filter as one ``FIELD=<code>`` pair."""
    code = _exactly_one(flow, field_name, values)
    _require_known_code(flow, field_name, code, filter_map)
    return [(condition.html_base, code)]


def _encode_multi(
    flow: str,
    field_name: str,
    values: list[str],
    filter_map: FilterMap,
    condition: FieldCondition,
) -> list[tuple[str, str]]:
    """Encode a ``multi`` filter as repeated ``FIELD[]=<code>`` pairs.

    The multi-value wrapper applies the ``[]`` suffix to the DOM base name so
    the server and the dump HTML agree (e.g. ``MADORI[]=km002``).
    """
    for code in values:
        _require_known_code(flow, field_name, code, filter_map)
    return [(f"{condition.html_base}[]", code) for code in values]


def _encode_range(
    flow: str,
    pair: tuple[str, str],
    values: list[str],
    filter_map: FilterMap,
) -> list[tuple[str, str]]:
    """Encode a range filter as FROM and TO single-field pairs.

    A sentinel option (e.g. "No lower limit"/"No upper limit") carries a real
    code in the map, so the pair is always exactly two codes. The bounds are
    validated against each side's option list independently.
    """
    from_code, to_code = _range_pair(flow, values)
    from_field, to_field = pair
    _require_known_code(flow, from_field, from_code, filter_map)
    _require_known_code(flow, to_field, to_code, filter_map)
    return [(from_field, from_code), (to_field, to_code)]


def _encode_bool(
    flow: str,
    field_name: str,
    values: list[str],
    filter_map: FilterMap,
    condition: FieldCondition,
) -> list[tuple[str, str]]:
    """Encode a bool toggle, returning zero pairs when the toggle is off.

    On: emit the field's single option code. For checkbox controls the
    submission shape is ``FIELD[]=code`` (matching the rent page's
    ``APPEAL[]``/``REFORM[]`` controls); for select-like controls it is
    ``FIELD=code``. Off omits the parameter entirely.
    """
    enabled = _bool_value(flow, field_name, values)
    if not enabled:
        return []
    code = _single_bool_code(flow, field_name, filter_map)
    if condition.control == "checkbox":
        return [(f"{condition.html_base}[]", code)]
    return [(condition.html_base, code)]


def _single_bool_code(flow: str, field_name: str, filter_map: FilterMap) -> str:
    """Return the single option code of a ``bool`` field; verifies existence."""
    options = _field_options(flow, field_name, filter_map)
    if len(options) != 1:
        raise UnknownFilter(
            f"{flow}.{field_name}: bool field must expose exactly one option, found {len(options)}"
        )
    return options[0].code


def _field_options(flow: str, field_name: str, filter_map: FilterMap) -> list[FilterOption]:
    """Return the ordered options for ``(flow, field_name)`` from the snapshot.

    The snapshot stores options as pydantic :class:`FilterOption` objects, so
    the returned list is read-only input for the code-presence checks.
    """
    flow_map = filter_map.mappings.get(flow)
    if flow_map is None:
        raise UnknownFilter(f"flow '{flow}' missing from filter map")
    options = flow_map.get(field_name)
    if options is None:
        raise UnknownFilter(f"flow '{flow}' has no options for filter '{field_name}'")
    return options


def _require_known_code(flow: str, field_name: str, code: str, filter_map: FilterMap) -> None:
    """Raise if ``code`` is not a selectable option of ``(flow, field)``."""
    options = _field_options(flow, field_name, filter_map)
    if any(option.code == code for option in options):
        return
    raise UnknownFilterValue(f"{flow}.{field_name}: unknown code '{code}'")


def _exactly_one(flow: str, field_name: str, values: list[str]) -> str:
    """Return the single selected code of a ``single`` filter or raise."""
    if len(values) != 1:
        raise UnknownFilterValue(
            f"{flow}.{field_name}: expected exactly one selection, got {len(values)}"
        )
    return values[0]


def _range_pair(flow: str, values: list[str]) -> tuple[str, str]:
    """Return ``(from_code, to_code)`` for a range filter or raise."""
    if len(values) != 2:
        raise UnknownFilterValue(
            f"{flow}: price range must carry exactly two codes, got {len(values)}"
        )
    return values[0], values[1]


def _bool_value(flow: str, field_name: str, values: list[str]) -> bool:
    """Parse a bool filter value list into a Python bool, or raise.

    Accepts the documented toggle spellings and raises :class:`UnknownFilterValue`
    for anything else so the encoder never guesses whether a toggle is on.
    """
    if len(values) != 1:
        raise UnknownFilterValue(
            f"{flow}.{field_name}: bool filter expects one toggle, got {len(values)}"
        )
    raw = values[0].strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    raise UnknownFilterValue(f"{flow}.{field_name}: unparseable bool toggle '{values[0]}'")
