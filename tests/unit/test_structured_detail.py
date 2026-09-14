"""Fixture-backed T30 structured detail and persistence tests."""

from __future__ import annotations

from pathlib import Path

from athome_harness.models import Agency, ListingDetail, PriceBreakdown
from athome_harness.scraping.detail_parser import parse_detail_page
from athome_harness.scraping.server_app_state import extract_server_app_rich_detail
from athome_harness.store.sqlite_store import SqliteStore

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "server-app-state-1106831830.full.json"


def _html() -> str:
    """Wrap the curated state fixture in the script consumed by the parser."""
    return (
        '<title>Example [1106831830]</title><script id="serverApp-state">'
        f"{FIXTURE.read_text()}</script>"
    )


def test_full_state_extracts_rich_detail_and_agency() -> None:
    """The full fixture exposes agency, Romanized, media, and facility data."""
    result = extract_server_app_rich_detail(_html(), "1106831830")
    assert result is not None
    rich, agency = result
    assert agency is not None
    assert agency.kaiin_no == "50109693"
    assert agency.business_hours == "10:00～19:00"
    assert rich.romanized["cityRoman"] == "osaka_chuo"
    assert len(rich.images) == 39
    assert len(rich.nearby_facilities) == 8
    assert len(rich.feature_groups) == 19
    assert rich.access[0].walk_time == "6分"
    assert rich.cost_info["initialCostSimulation"]["preliminary"] == "238,900"
    assert rich.appeal_point
    assert rich.other_property_info["bukkenNo"] == "1106831830"


def test_detail_parser_and_store_preserve_rich_state_and_agency() -> None:
    """A parsed detail round-trips through SQLite with its agency link."""
    detail = parse_detail_page(_html())
    assert isinstance(detail, ListingDetail)
    assert detail.agency is not None
    assert detail.structured_detail is not None
    store = SqliteStore(":memory:")
    try:
        assert store.upsert_listing(detail) == detail.internal_id
        loaded = store.get_listing(detail.internal_id)
        assert isinstance(loaded, ListingDetail)
        assert loaded.agency == detail.agency
        assert loaded.structured_detail is not None
        assert len(loaded.structured_detail.images) == 39
        assert loaded.structured_detail.romanized["stationRoman"] == "tanimachirokuchome"
        assert store.get_agency("50109693") == detail.agency
    finally:
        store.close()


def test_sparse_updates_do_not_erase_existing_rich_values() -> None:
    """Sparse agency and listing updates retain complete prior fields."""
    detail = parse_detail_page(_html())
    store = SqliteStore(":memory:")
    try:
        store.upsert_listing(detail)
        store.upsert_agency(Agency(kaiin_no="50109693", name=None, phone=None))
        sparse = ListingDetail(
            internal_id=detail.internal_id,
            athome_key=detail.athome_key,
            url=detail.url,
            title=detail.title,
            address=detail.address,
            price=PriceBreakdown(rent=0),
            area_m2=0,
        )
        store.upsert_listing(sparse)
        loaded = store.get_listing(detail.internal_id)
        assert isinstance(loaded, ListingDetail)
        assert loaded.structured_detail is not None
        assert len(loaded.structured_detail.nearby_facilities) == 8
        agency = store.get_agency("50109693")
        assert agency is not None
        assert agency.name == detail.agency.name
        assert agency.phone == detail.agency.phone
    finally:
        store.close()


def test_fixture_is_not_recommendation_ingestion() -> None:
    """The unsupported otherPropertyData remains only in raw state, not listings."""
    result = extract_server_app_rich_detail(_html(), "1106831830")
    assert result is not None
    rich, _ = result
    assert "otherPropertyData" not in rich.raw
