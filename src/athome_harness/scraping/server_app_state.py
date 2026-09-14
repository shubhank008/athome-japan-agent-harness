"""Validated extraction of AtHome's embedded ``serverApp-state`` detail payload."""

from __future__ import annotations

import json
from typing import Any

from selectolax.parser import HTMLParser

from athome_harness.models import (
    AccessRecord,
    Agency,
    FacilityRecord,
    FeatureGroup,
    ImageRecord,
    RecommendationCard,
    StructuredDetail,
)


def extract_server_app_detail(html: str, expected_id: str) -> dict[str, Any] | None:
    """Return structured detail data when the embedded payload is usable.

    The SSR state is an optimization and richer source, not a trusted API. This
    function validates the stable ``first-view-ITEMS.propertyData.rentInfo``
    path and the requested listing identity before returning it. Missing or
    changed wrappers return ``None`` so callers can use the DOM parser.
    """
    script = HTMLParser(html).css_first("script#serverApp-state")
    if script is None:
        return None
    try:
        payload = json.loads(script.text())
    except (TypeError, json.JSONDecodeError):
        return None
    try:
        rent_info = payload["first-view-ITEMS"]["propertyData"]["rentInfo"]
    except (KeyError, TypeError):
        return None
    if not isinstance(rent_info, dict):
        return None
    other = rent_info.get("otherPropertyInfo")
    if not isinstance(other, dict) or str(other.get("bukkenNo", "")) != expected_id:
        return None
    required = (rent_info.get("buildingNm"), rent_info.get("price"))
    if not all(isinstance(value, str) and value.strip() for value in required):
        return None
    return rent_info


def extract_server_app_rich_detail(
    html: str, expected_id: str
) -> tuple[StructuredDetail, Agency | None] | None:
    """Extract typed rich detail and agency records from validated SSR state."""
    state = extract_server_app_detail(html, expected_id)
    if state is None:
        return None
    script = HTMLParser(html).css_first("script#serverApp-state")
    if script is None:
        return None
    try:
        property_data = json.loads(script.text())["first-view-ITEMS"]["propertyData"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(property_data, dict):
        return None
    images = [
        ImageRecord(
            url=str(item["imageUrl"]),
            title=item.get("title"),
            category=item.get("subCategory"),
            raw=item,
        )
        for item in state.get("imageList", [])
        if isinstance(item, dict) and item.get("imageUrl")
    ]
    surrounding = property_data.get("surroundingInfo")
    surrounding = surrounding if isinstance(surrounding, dict) else {}
    nearby = [
        FacilityRecord(
            title=str(item["title"]),
            category=item.get("shubetsuNm"),
            distance=item.get("kyori"),
            image_url=item.get("imageURL"),
            raw=item,
        )
        for item in surrounding.get("facilityList", [])
        if isinstance(item, dict) and item.get("title")
    ]
    access = state.get("access") or surrounding.get("accessInfo", [])
    access_records = [
        AccessRecord(
            line_name=item.get("lineName") or item.get("name"),
            station_name=item.get("stationName"),
            walk_time=item.get("tohoJikan") or item.get("accessEkiToho"),
            raw=item,
        )
        for item in access
        if isinstance(item, dict)
    ]
    feature_groups = [
        FeatureGroup(title=str(item["title"]), text=str(item.get("text", "")), raw=item)
        for item in property_data.get("facilityFeatureList", [])
        if isinstance(item, dict) and item.get("title")
    ]
    agency = _agency_from_kaiin_info(property_data.get("kaiinInfo"))
    rich = StructuredDetail(
        raw=state,
        romanized={k: v for k, v in state.items() if k.endswith("Roman") and isinstance(v, str)},
        access=access_records,
        images=images,
        nearby_facilities=nearby,
        feature_groups=feature_groups,
        surrounding_info=surrounding,
        cost_info=property_data.get("costInfo", {})
        if isinstance(property_data.get("costInfo"), dict)
        else {},
        appeal_point=state.get("appealPoint")
        if isinstance(state.get("appealPoint"), str)
        else None,
        building_info=state.get("buildingInfo", {})
        if isinstance(state.get("buildingInfo"), dict)
        else {},
        other_property_info=state.get("otherPropertyInfo", {})
        if isinstance(state.get("otherPropertyInfo"), dict)
        else {},
    )
    return rich, agency


def extract_server_app_recommendation_cards(
    html: str, expected_id: str
) -> list[RecommendationCard]:
    """Return recommendation cards from validated target detail state only."""
    state = extract_server_app_detail(html, expected_id)
    if state is None:
        return []
    script = HTMLParser(html).css_first("script#serverApp-state")
    if script is None:
        return []
    try:
        property_data = json.loads(script.text())["first-view-ITEMS"]["propertyData"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return []
    cards = property_data.get("otherPropertyData") if isinstance(property_data, dict) else None
    if not isinstance(cards, list):
        return []
    return [
        RecommendationCard(
            athome_key=str(card["id"]),
            title=str(card.get("title") or ""),
            seo_path=str(card.get("seoRoma") or "chintai"),
            location=str(card.get("location") or ""),
            raw=card,
        )
        for card in cards
        if isinstance(card, dict) and card.get("id")
    ]



def _agency_from_kaiin_info(value: object) -> Agency | None:
    """Convert selected agency contact and operational fields."""
    if not isinstance(value, dict) or not value.get("kaiinNo"):
        return None
    hours = value.get("eigyoTime")
    address = value.get("address")
    postal_code = (
        address.split(" ", 1)[0].removeprefix("〒")
        if isinstance(address, str) and address.startswith("〒")
        else None
    )
    return Agency(
        kaiin_no=str(value["kaiinNo"]),
        kaiin_link_no=str(value["kaiinLinkNo"]) if value.get("kaiinLinkNo") else None,
        name=value.get("syogo"),
        postal_code=postal_code,
        address=address,
        phone=value.get("telFax"),
        url=value.get("syosaiUrl"),
        domain=value.get("domain"),
        access=value.get("access"),
        business_hours=hours.get("main") if isinstance(hours, dict) else None,
        holidays=value.get("teikyubi"),
        features=value.get("tokutyou"),
        associations=value.get("syozokuKyokai"),
        license_number=value.get("menkyoNo"),
        raw=value,
    )


def extract_server_app_agency(html: str) -> Agency | None:
    """Extract the agency from canonical structured detail state."""
    script = HTMLParser(html).css_first("script#serverApp-state")
    if script is None:
        return None
    try:
        payload = json.loads(script.text())
        property_data = payload.get("first-view-ITEMS", {}).get("propertyData", {})
        raw = property_data.get("kaiinInfo")
        if not isinstance(raw, dict):
            raw = payload.get("listing", {}).get("agency")
    except (TypeError, AttributeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not str(raw.get("kaiinNo", "")).strip():
        return None
    hours = raw.get("eigyoTime")
    main_hours = hours.get("main") if isinstance(hours, dict) else None
    def text(value: object) -> str | None:
        """Normalize optional agency text fields."""
        result = str(value).strip() if value is not None else ""
        return result or None
    return Agency(
        kaiin_no=str(raw["kaiinNo"]),
        kaiin_link_no=text(raw.get("kaiinLinkNo")),
        name=text(raw.get("syogo")),
        address=text(raw.get("address")),
        phone=text(raw.get("telFax")),
        url=text(raw.get("syosaiUrl") or raw.get("urlLong")),
        domain=text(raw.get("domain")),
        access=text(raw.get("access")),
        business_hours=text(main_hours),
        holidays=text(raw.get("teikyubi")),
        features=text(raw.get("tokutyou")),
        associations=text(raw.get("syozokuKyokai")),
        license_number=text(raw.get("menkyoNo")),
        raw=raw,
    )
