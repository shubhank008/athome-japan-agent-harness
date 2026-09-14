"""Compact property payloads shared by the LLM ranking stages."""

from __future__ import annotations

from typing import Any

from athome_harness.models import ListingDetail, ListingSummary

# Keep free text bounded so one unusually verbose detail page cannot dominate a prompt.
MAX_PROPERTY_TEXT_CHARS = 1200


def project_property_for_llm(listing: ListingSummary | ListingDetail) -> dict[str, Any]:
    """Return the ranking-oriented property projection sent to an LLM."""
    data: dict[str, Any] = {
        "athome_key": listing.athome_key,
        "title": listing.title,
        "address": listing.address,
        "station": listing.station,
        "walk_minutes": listing.walk_minutes,
        "price": listing.price.model_dump(mode="json"),
        "floor_plan": listing.floor_plan,
        "area_m2": listing.area_m2,
        "building_type": listing.building_type,
        "floors": listing.floors,
        "construction_date": listing.construction_date or listing.age_display,
        "usp_tags": listing.usp_tags,
        "probable_negatives": listing.probable_negatives,
    }

    if isinstance(listing, ListingDetail):
        data.update(
            {
                "pickup_features": listing.pickup_features,
                "facility_features": listing.facility_features,
                "description": _consolidate_text(listing.description, listing.remarks),
            }
        )

    return _without_empty_values(data)


def _consolidate_text(description: str, remarks: str | None) -> str | None:
    """Bound detail text and preserve both sources when they differ."""
    bounded_description = _bound_text(description)
    bounded_remarks = _bound_text(remarks)
    if bounded_description and bounded_remarks:
        if bounded_description == bounded_remarks:
            return bounded_description
        return f"Description: {bounded_description}\nRemarks: {bounded_remarks}"
    return bounded_description or bounded_remarks


def _bound_text(value: str | None) -> str | None:
    """Return bounded, whitespace-normalized text or ``None`` when empty."""
    if not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:MAX_PROPERTY_TEXT_CHARS] or None


def _without_empty_values(data: dict[str, Any]) -> dict[str, Any]:
    """Drop optional null and empty values without changing nested price data."""
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != "" and value != []
    }
