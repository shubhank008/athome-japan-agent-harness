"""Dump tool: extract the versioned AtHome filter map from live reference pages (T11).

The harness never ships a hand-maintained filter list; instead a weekly job
extracts the ``(flow, field) -> [{code, label}]`` map from two canary pages:

  * rent  -> ``https://www.athome.co.jp/chintai/osaka/list/``
  * buy   -> ``https://www.athome.co.jp/mansion/tokyo/list/``

Every control the encoder needs lives on those server-rendered pages, so a page
that no longer exposes a required control is a hard failure: the tool emits
``[FILTERMAP_BROKEN]`` and exits non-zero instead of silently writing a map
without the filter.

The extraction and validation functions take only HTML text and the
:data:`CONDITIONS` metadata, so unit tests exercise them with fixture files and
an injected fetcher, with no live network. The production path routes through
the M1 :class:`HttpDomAdapter` (:class:`BaseScraper`), the only module allowed
to import a third-party HTTP library per the Abstract First invariant.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from athome_harness.filters.map_schema import (
    CONDITIONS,
    FLOWS,
    SUPPORTED_SCHEMA_VERSION,
    FilterMapSchemaViolation,
    canonical_payload,
    compute_content_hash,
    validate,
)
from athome_harness.models import FilterMap, FilterOption
from athome_harness.scraping.base import BlockDetected, redact_url

logger = logging.getLogger(__name__)

# Canonical canary URLs. ``flow -> (url, label)``; the label describes the page
# in reports without embedding the URL (marker contract forbids full URLs with
# query strings in logs).
CANARY_URLS: dict[str, tuple[str, str]] = {
    "rent": ("https://www.athome.co.jp/chintai/osaka/list/", "rental Osaka"),
    "buy": ("https://www.athome.co.jp/mansion/tokyo/list/", "purchase Tokyo"),
}

# Default snapshot location, relative to the repo root.
DEFAULT_OUTPUT = Path("filters/data/filter_map.v1.json")

# Select option values that carry no filter meaning and are skipped.
_SKIP_SELECT_VALUE = ""
# Control names that are UI plumbing rather than user filters on the buy page.
_NON_FILTER_CHECKBOX_NAMES = frozenset(
    {"all-check[]", "list[]", "bukken", "defalt-check[]"}
)


class FilterMapExtractionError(RuntimeError):
    """Raised when a required control is missing or empty on a canary page.

    The message is designed to be printed as-is in an issue report and starts
    with the ``[FILTERMAP_BROKEN]`` marker so failures are grep-able.
    """


FetchFn = Callable[[str], str]


def extract_flow(html: str, flow: str) -> dict[str, list[FilterOption]]:
    """Extract ``field -> options`` for one flow from server-rendered HTML.

    Uses :data:`CONDITIONS` to know which DOM control family each field belongs
    to (``select`` vs ``checkbox``) instead of guessing from HTML. A ``range``
    field is a logical field over real HTML selects; it is skipped here and its
    FROM/TO parts are extracted via their own single conditions.

    Raises :class:`FilterMapExtractionError` with ``reason=selector`` when a
    required control is missing, or ``reason=empty`` when it yields no options.
    """
    from bs4 import BeautifulSoup  # lazy: only extraction needs it

    soup = BeautifulSoup(html, "html.parser")
    extracted: dict[str, list[FilterOption]] = {}
    for field_name, condition in CONDITIONS[flow].items():
        if condition.cardinality == "range":
            continue
        options = _extract_field(soup, field_name, condition)
        if options is None:
            raise FilterMapExtractionError(
                f"[FILTERMAP_BROKEN] flow={flow} filter={field_name} reason=selector"
            )
        if not options:
            raise FilterMapExtractionError(
                f"[FILTERMAP_BROKEN] flow={flow} filter={field_name} reason=empty"
            )
        extracted[field_name] = options
    return extracted


def _extract_field(
    soup: Any, field_name: str, condition: Any
) -> list[FilterOption] | None:
    """Extract one field's options, or None when the control is absent."""
    if condition.control == "select":
        return _extract_select(soup, field_name, condition)
    return _extract_checkboxes(soup, field_name, condition)


def _extract_select(
    soup: Any, field_name: str, condition: Any
) -> list[FilterOption] | None:
    """Extract options of a single ``<select name=...>`` control.

    ``None`` means the control is absent (``reason=selector``); ``[]`` means
    the control exists but exposes no options (``reason=empty``).
    """
    base = condition.html_base
    select = None
    for el in soup.find_all("select"):
        if (el.get("name") or "").rstrip("[]") == base:
            select = el
            break
    if select is None:
        return None
    options: list[FilterOption] = []
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        label = re.sub(r"\s+", " ", opt.get_text(" ", strip=True)).strip()
        if value == _SKIP_SELECT_VALUE or not value:
            continue
        options.append(FilterOption(code=value, label=label))
    return options


def _extract_checkboxes(
    soup: Any, field_name: str, condition: Any
) -> list[FilterOption] | None:
    """Extract options of a checkbox/radio group by DOM base name.

    Two naming schemes occur in the wild:

      * rent: every control in the group is named ``FIELD[]`` (or ``FIELD``);
      * buy: every checkbox is named after its own ``value`` code (e.g.
        ``name="km002"``), with no shared group name.

    A control matches when its name equals ``html_base``/``html_base[]``, or
    when its name equals its value and the value matches the field's code
    regex (buy style). Option values are the codes; labels come from the
    associated ``<label>`` element.
    """
    base = condition.html_base
    regex = condition.code_regex
    matches: list[Any] = []
    for el in soup.find_all("input", attrs={"type": ("checkbox", "radio")}):
        name = el.get("name")
        code = el.get("value")
        if _matches_name(name, base):
            matches.append(el)
        elif (
            regex is not None
            and name
            and code
            and name == code
            and regex.match(code)
        ):
            matches.append(el)
    if not matches:
        return None
    options: list[FilterOption] = []
    for el in matches:
        code = el.get("value")
        if not code:
            continue
        label = _control_label(soup, el)
        if code in _NON_FILTER_CHECKBOX_NAMES:
            continue
        options.append(FilterOption(code=code, label=label))
    return options


def _matches_name(name: str | None, base: str) -> bool:
    """True when a control's name equals ``base`` or ``base + []``."""
    if not name:
        return False
    return name == base or name == base + "[]"


def _control_label(soup: Any, control: Any) -> str:
    """Return the human label attached to a checkbox/radio control.

    Prefers the wrapping ``<label>``, then a ``<label for=...>`` element, and
    strips trailing ``(count)`` suffixes AtHome renders next to some labels.
    """
    label_el = control.find_parent("label")
    if label_el is None and control.get("id"):
        label_el = soup.find("label", attrs={"for": control["id"]})
    if label_el is None:
        return ""
    text = re.sub(r"\s+", " ", label_el.get_text(" ", strip=True)).strip()
    # Drop trailing "(22,134)"-style listing counts and empty "()" artifacts.
    text = re.sub(r"\(\s*[\d,]+\s*\)\s*$", "", text)
    return re.sub(r"\(\)\s*$", "", text).strip()


def extract_canaries(fetch: FetchFn) -> dict[str, dict[str, list[FilterOption]]]:
    """Fetch both canary pages through ``fetch`` and extract per-flow maps.

    ``fetch(url) -> html`` is injected by the caller: the production path uses
    the M1 :class:`HttpDomAdapter`, tests use local fixture files. Extraction
    errors are re-raised for an issue-ready report; the redacted URL is logged.
    """
    logger.info("[FILTERMAP_START] flows=rent,buy")
    maps: dict[str, dict[str, list[FilterOption]]] = {}
    for flow in ("rent", "buy"):
        url, _label = CANARY_URLS[flow]
        html = fetch(url)
        try:
            maps[flow] = extract_flow(html, flow)
        except FilterMapExtractionError as exc:
            logger.error("%s url=<%s>", exc, redact_url(url))
            raise
    return maps


def build_map(fetched: dict[str, dict[str, list[FilterOption]]]) -> FilterMap:
    """Build and validate a :class:`FilterMap` from extracted mappings.

    Computes the deterministic content hash, then validates the schema so a
    broken extraction surfaces before anything is written to disk.
    """
    content_hash = compute_content_hash(fetched)
    filter_map = FilterMap(
        version=SUPPORTED_SCHEMA_VERSION,
        content_hash=content_hash,
        mappings=fetched,
    )
    validate(filter_map)
    return filter_map


def write_map(filter_map: FilterMap, path: str | Path) -> None:
    """Write the validated map as stable, sorted JSON to ``path``.

    The on-disk body carries the ``content_hash`` alongside the canonical
    ``version``/``mappings`` payload, so a hash recomputed from the file
    always matches the stored ``content_hash``.
    """
    path = Path(path)
    payload = json.loads(canonical_payload(filter_map.version, filter_map.mappings))
    payload["content_hash"] = filter_map.content_hash
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def check_map(path: str | Path) -> tuple[int, str]:
    """Validate an on-disk snapshot and return ``(exit_code, report)``.

    Used by ``--check``: exit nonzero when the file is missing, malformed,
    hash-mismatched, or fails schema validation, and produce an issue-ready
    report. A valid map yields ``(0, [FILTERMAP_OK] ...)``.
    """
    path = Path(path)
    if not path.exists():
        return 2, f"[FILTERMAP_BROKEN] reason=schema no snapshot at {path}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        content_hash = str(raw.get("content_hash", ""))
        filter_map = FilterMap.model_validate(raw)
        validate(filter_map)
    except (json.JSONDecodeError, ValueError) as exc:
        return 1, f"[FILTERMAP_BROKEN] reason=schema error: {exc}"
    expected = compute_content_hash(filter_map.mappings)
    if content_hash != expected:
        return 1, (
            f"[FILTERMAP_BROKEN] reason=schema hash mismatch "
            f"stored={content_hash} recomputed={expected}"
        )
    counts = {flow: len(filter_map.mappings.get(flow, {})) for flow in FLOWS}
    return 0, (
        f"[FILTERMAP_OK] version={filter_map.version} "
        f"filters_rent={counts['rent']} filters_buy={counts['buy']} "
        f"hash={content_hash}"
    )


def _default_fetcher() -> FetchFn:
    """Return the production fetcher from the configured scraper provider."""
    from athome_harness.providers import build_production_fetch

    return build_production_fetch()


def default_fetcher() -> FetchFn:
    """Public alias for the production fetcher (used by the CLI)."""
    return _default_fetcher()


def cmd_dump(args: argparse.Namespace) -> int:
    """Fetch both canaries, validate, and write the snapshot JSON.

    A fetch that surfaces :class:`BlockDetected` (for example an AtHome
    challenge page, which is classified as a captcha block by the HTTP adapter)
    exits nonzero without writing any file, so challenge HTML can never become a
    map fixture.
    """
    fetch = default_fetcher()
    try:
        fetched = extract_canaries(fetch)
        filter_map = build_map(fetched)
    except (FilterMapExtractionError, FilterMapSchemaViolation, BlockDetected) as exc:
        logger.error("%s", exc)
        return 1
    write_map(filter_map, args.output)
    counts = {flow: len(filter_map.mappings.get(flow, {})) for flow in FLOWS}
    logger.info(
        "[FILTERMAP_OK] version=%d filters_rent=%d filters_buy=%d hash=%s",
        filter_map.version, counts["rent"], counts["buy"], filter_map.content_hash,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate an on-disk map; exit nonzero on schema/extraction failure."""
    code, report = check_map(args.output)
    logger.info("%s", report)
    return code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: dump (default) or ``--check`` validation mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="where to write the map JSON (default: filters/data/filter_map.v1.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate an existing snapshot instead of fetching",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.check:
        return cmd_check(args)
    return cmd_dump(args)


if __name__ == "__main__":
    raise SystemExit(main())
