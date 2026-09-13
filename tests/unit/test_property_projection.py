"""Tests for the compact property payload shared by both ranking stages."""

from __future__ import annotations

import json
from pathlib import Path

from athome_harness.llm.property_projection import project_property_for_llm
from athome_harness.llm.recommender import Recommender
from athome_harness.llm.shortlister import Shortlister
from athome_harness.models import ListingDetail, PriceBreakdown, SearchPlan
from athome_harness.store.sqlite_store import SqliteStore
from tests.unit._fakes import SequenceProvider


def _detail(**overrides: object) -> ListingDetail:
    """Build a detail containing every field relevant to projection tests."""
    fields: dict[str, object] = {
        "internal_id": "internal-1",
        "athome_key": "BK-1",
        "url": "https://example.test/listing",
        "title": "Sunny apartment",
        "address": "Osaka",
        "station": "Umeda",
        "walk_minutes": 5.0,
        "building_type": "Apartment",
        "floors": "10 floors",
        "age": 8.0,
        "age_raw": "築8年",
        "construction_date": "2018-04",
        "age_display": "8 years old",
        "price": PriceBreakdown(rent=100_000),
        "floor_plan": "1LDK",
        "area_m2": 40.0,
        "usp_tags": ["corner"],
        "probable_negatives": ["no parking"],
        "photo_urls": ["https://example.test/photo"],
        "listing_detail": True,
        "detail_failure_reason": None,
        "building_name": "Sunrise",
        "building_structure": "RC",
        "total_units": "50",
        "contract_period": "2 years",
        "pickup_features": ["bath dryer"],
        "remarks": "Quiet room",
        "description": "Bright room",
        "floor_plan_image_url": "https://example.test/floorplan",
        "facility_features": ["auto-lock"],
    }
    fields.update(overrides)
    return ListingDetail(**fields)


def test_projection_filters_internal_and_operational_fields() -> None:
    """The LLM payload excludes user-approved non-ranking fields."""
    projected = project_property_for_llm(_detail())
    removed = {
        "agency",
        "agency_reference",
        "url",
        "detail_fetched_at",
        "detail_fresh_until",
        "completeness",
        "internal_id",
        "age",
        "age_raw",
        "age_display",
        "listing_detail",
        "detail_failure_reason",
    }
    assert removed.isdisjoint(projected)
    assert projected["athome_key"] == "BK-1"
    assert projected["construction_date"] == "2018-04"
    assert projected["pickup_features"] == ["bath dryer"]
    assert projected["facility_features"] == ["auto-lock"]


def test_projection_uses_age_display_when_construction_date_is_empty() -> None:
    """An absent construction date falls back to the human-readable age."""
    projected = project_property_for_llm(
        _detail(construction_date=None, age_display="1 month old")
    )
    assert projected["construction_date"] == "1 month old"


def test_projection_consolidates_identical_text() -> None:
    """Identical description and remarks appear once."""
    projected = project_property_for_llm(_detail(description="Same text", remarks="Same text"))
    assert projected["description"] == "Same text"


def test_projection_labels_different_text_without_loss() -> None:
    """Different text sources remain separately labeled in one field."""
    projected = project_property_for_llm(
        _detail(description="Description text", remarks="Remarks text")
    )
    assert projected["description"] == "Description: Description text\nRemarks: Remarks text"


def test_both_ranking_call_sites_use_projection(tmp_path: Path) -> None:
    """Shortlister and Recommender serialize the same compact property shape."""
    detail = _detail()
    shortlister = Shortlister(
        SequenceProvider(['{"entries": []}']), example_path=tmp_path / "example.json"
    )
    recommender = Recommender(SequenceProvider(['{"ranked": []}']))
    expected = project_property_for_llm(detail)
    assert json.loads(shortlister._serialize(detail)) == expected
    assert json.loads(recommender._serialize(detail)) == expected
    shortlister._write_example([detail])
    assert json.loads((tmp_path / "example.json").read_text(encoding="utf-8")) == expected


def test_llm_athome_key_maps_back_to_canonical_internal_id() -> None:
    """Ranking responses use the compact ID while reports use the internal ID."""
    detail = _detail()
    shortlister = Shortlister(
        SequenceProvider(
            ['{"entries": [{"listing_id": "BK-1", "score": 9, "rationale": "fit"}]}']
        )
    )
    shortlist = shortlister.shortlist([], [detail], top_x=1)
    assert shortlist[0].listing_id == "internal-1"

    recommender = Recommender(
        SequenceProvider(['{"ranked": [{"listing_id": "BK-1", "reasons": ["fit"]}]}'])
    )
    recommendations = recommender.recommend(
        [detail], SearchPlan(flow="rent", prefecture="osaka"), top_y=1
    )
    assert recommendations[0].listing_id == "internal-1"



def test_canonical_storage_remains_rich() -> None:
    """Projection is only for prompts; SQLite retains the complete detail model."""
    detail = _detail()
    store = SqliteStore(":memory:")
    try:
        store.upsert_listing(detail)
        loaded = store.get_listing(detail.internal_id)
        assert isinstance(loaded, ListingDetail)
        assert loaded.url == detail.url
        assert loaded.internal_id == detail.internal_id
        assert loaded.description == detail.description
        assert loaded.floor_plan_image_url == detail.floor_plan_image_url
    finally:
        store.close()
