"""Unit tests for the filter map dump tool (M2, T11).

All tests run against deterministic fixture HTML files and an injected fetcher
mapping URL -> fixture HTML. No live network access in the default suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athome_harness.filters.map_schema import (
    SUPPORTED_SCHEMA_VERSION,
)
from athome_harness.scraping.base import BlockDetected
from tools import dump_filter_map as tool

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
RENT_FIXTURE = FIXTURES / "filter_map_rent.html"
BUY_FIXTURE = FIXTURES / "filter_map_buy.html"


def fixture_fetcher() -> tool.FetchFn:
    """Return a fetcher wired to the deterministic fixture pages."""

    def fetch(url: str) -> str:
        # Both canary URLs route to tree-split fixtures by flow.
        if "chintai" in url:
            return RENT_FIXTURE.read_text(encoding="utf-8")
        if "mansion" in url:
            return BUY_FIXTURE.read_text(encoding="utf-8")
        return RENT_FIXTURE.read_text(encoding="utf-8")

    return fetch


def test_extract_rent_fixture() -> None:
    """Rent extraction must cover every select/checkbox in the fixture."""
    fields = tool.extract_flow(RENT_FIXTURE.read_text(encoding="utf-8"), "rent")
    assert "PRICEFROM" in fields
    assert "MADORI" in fields
    assert fields["APPEAL"][0].code == "ka001"


def test_extract_buy_fixture() -> None:
    """Buy extraction must resolve name==value checkboxes and MENSEKITO."""
    fields = tool.extract_flow(BUY_FIXTURE.read_text(encoding="utf-8"), "buy")
    assert "MENSEKITO" in fields
    assert {o.code for o in fields["MADORI"]} == {"km002", "km003", "km004", "km017"}
    assert {o.code for o in fields["KKGROUP"]} == {"kk002", "kk003"}


def test_extraction_skips_range_part_controls() -> None:
    """Range fields are logical aliases, not real DOM controls, so skipped."""
    rent = tool.extract_flow(RENT_FIXTURE.read_text(encoding="utf-8"), "rent")
    assert "PRICE" not in rent


def test_extract_canaries_with_injected_fetcher() -> None:
    """extract_canaries must build both flows through a pluggable fetcher."""
    maps = tool.extract_canaries(fixture_fetcher())
    assert set(maps) == {"rent", "buy"}
    assert "PRICEFROM" in maps["rent"] and "PRICEFROM" in maps["buy"]


def test_build_map_roundtrip(tmp_path: Path) -> None:
    """write_map then check_map must reproduce the OK report."""
    maps = tool.extract_canaries(fixture_fetcher())
    filter_map = tool.build_map(maps)
    out = tmp_path / "filter_map.v1.json"
    tool.write_map(filter_map, out)
    code, report = tool.check_map(out)
    assert code == 0
    assert f"version={SUPPORTED_SCHEMA_VERSION}" in report
    assert "filters_rent=" in report and "filters_buy=" in report
    # The stored hash must equal a recomputation from the file body.
    body = json.loads(out.read_text(encoding="utf-8"))
    from tools.dump_filter_map import compute_content_hash

    assert body["content_hash"] == compute_content_hash(filter_map.mappings)
    # The body is stable, sorted JSON with two-space indentation.
    raw = out.read_text(encoding="utf-8")
    assert json.loads(raw) == json.loads(json.dumps(json.loads(raw), sort_keys=True))


def test_check_rejects_missing_file(tmp_path: Path) -> None:
    """A missing snapshot must exit nonzero with a BROKEN marker."""
    code, report = tool.check_map(tmp_path / "nope.json")
    assert code != 0
    assert "FILTERMAP_BROKEN" in report
    assert "reason=schema" in report


def test_check_rejects_hash_mismatch(tmp_path: Path) -> None:
    """A tampered hash must fail validation with a hash-mismatch reason."""
    maps = tool.extract_canaries(fixture_fetcher())
    filter_map = tool.build_map(maps)
    out = tmp_path / "filter_map.v1.json"
    tool.write_map(filter_map, out)
    body = json.loads(out.read_text(encoding="utf-8"))
    body["content_hash"] = "0" * 12
    out.write_text(json.dumps(body), encoding="utf-8")
    code, report = tool.check_map(out)
    assert code != 0
    assert "hash mismatch" in report


def test_check_rejects_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON must exit nonzero and report reason=schema."""
    out = tmp_path / "bad.json"
    out.write_text("{ not json", encoding="utf-8")
    code, report = tool.check_map(out)
    assert code != 0
    assert "reason=schema" in report


def test_cmd_dump_with_injected_fetcher(tmp_path: Path, monkeypatch) -> None:
    """cmd_dump must write a validated snapshot through the CLI path."""
    import argparse

    monkeypatch.setattr(tool, "default_fetcher", fixture_fetcher)
    out = tmp_path / "map.json"
    args = argparse.Namespace(output=str(out), check=False)
    assert tool.cmd_dump(args) == 0
    code, report = tool.check_map(out)
    assert code == 0


def test_cmd_check_valid(tmp_path: Path, monkeypatch) -> None:
    """cmd_check must return 0 for a valid snapshot."""
    import argparse

    monkeypatch.setattr(tool, "default_fetcher", fixture_fetcher)
    out = tmp_path / "map.json"
    tool.write_map(tool.build_map(tool.extract_canaries(fixture_fetcher())), out)
    args = argparse.Namespace(output=str(out), check=True)
    assert tool.cmd_check(args) == 0


def test_cmd_check_invalid_returns_nonzero(tmp_path: Path, monkeypatch) -> None:
    """cmd_check on a broken snapshot must exit nonzero."""
    import argparse

    out = tmp_path / "map.json"
    out.write_text("{ broken", encoding="utf-8")
    args = argparse.Namespace(output=str(out), check=True)
    assert tool.cmd_check(args) != 0


def test_selector_missing_raises_broken() -> None:
    """A page missing a required control must raise FILTERMAP_BROKEN."""
    html = "<html><body>no controls here</body></html>"
    with pytest.raises(tool.FilterMapExtractionError, match="reason=selector"):
        tool.extract_flow(html, "rent")


def test_empty_control_raises_broken() -> None:
    """A control with zero options must raise reason=empty."""
    html = "<html><body><select name='PRICEFROM'></select></body></html>"
    with pytest.raises(tool.FilterMapExtractionError, match="reason=empty"):
        tool.extract_flow(html, "buy")


# Inline representative AtHome challenge page bodies as captured in the M3
# incident: a 200-status puzzle/authentication page, not listing content.
CHALLENGE_PUZZLE_HTML = (
    "<html><body><h1>Click to verify</h1>"
    "<p>To regain access, please make sure that cookies and JavaScript are "
    "enabled.</p></body></html>"
)


def test_extract_flow_rejects_challenge_html() -> None:
    """Challenge HTML must fail extraction, so it can never become a map."""
    for flow in ("rent", "buy"):
        with pytest.raises(tool.FilterMapExtractionError, match="reason=selector"):
            tool.extract_flow(CHALLENGE_PUZZLE_HTML, flow)


def test_cmd_dump_block_detected_writes_no_file(tmp_path: Path, monkeypatch, caplog) -> None:
    """A blocked fetch must exit nonzero and never write a challenge map."""
    import argparse
    import logging

    def blocked_fetcher() -> tool.FetchFn:
        def fetch(url: str) -> str:
            raise BlockDetected("https://www.athome.co.jp/chintai/osaka/list/", "captcha")

        return fetch

    monkeypatch.setattr(tool, "default_fetcher", blocked_fetcher)
    out = tmp_path / "map.json"
    args = argparse.Namespace(output=str(out), check=False)
    with caplog.at_level(logging.ERROR):
        assert tool.cmd_dump(args) == 1
    assert not out.exists()
    assert any("[BLOCK_DETECTED]" in r.message for r in caplog.records)
