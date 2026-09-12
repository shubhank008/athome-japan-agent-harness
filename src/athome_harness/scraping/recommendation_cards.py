"""Normalization and persistence helpers for AtHome recommendation cards."""

from __future__ import annotations

import re
from typing import Any

from athome_harness.models import (
    HydrationIntent,
    ListingCompleteness,
    ListingSummary,
    PriceBreakdown,
    RecommendationCard,
)
from athome_harness.store.base import BaseDataStore

_BASE_URL = "https://www.athome.co.jp"
_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


class RecommendationIngestionResult:
    """Normalized candidates and non-durable hydration intents from one payload."""

    def __init__(
        self, summaries: list[ListingSummary], hydration_intents: list[HydrationIntent]
    ) -> None:
        """Initialize the result with deduplicated candidate lists."""
        self.summaries = summaries
        self.hydration_intents = hydration_intents


def normalize_recommendation_card(card: RecommendationCard) -> ListingSummary:
    """Map one typed recommendation card into the existing summary model."""
    raw = card.raw
    contract = _dict(raw.get("contract"))
    info = _dict(raw.get("bukkenInfo"))
    area_info = _dict(raw.get("areaInfo"))
    traffic = raw.get("traffic")
    first_traffic = _dict(traffic[0]) if isinstance(traffic, list) and traffic else {}
    url = f"{_BASE_URL}/{card.seo_path}/{card.athome_key}/"
    image_values = raw.get("images")
    image_list = image_values if isinstance(image_values, list) else []
    photos = [
        _absolute_image(_dict(image).get("url"))
        for image in image_list
        if isinstance(image, dict) and _dict(image).get("url")
    ]
    main_image = _dict(raw.get("mainImage")).get("url")
    if main_image and _absolute_image(main_image) not in photos:
        photos.insert(0, _absolute_image(main_image))
    location = card.location or str(raw.get("address") or "")
    return ListingSummary(
        internal_id=card.athome_key,
        completeness=ListingCompleteness.SUMMARY_COMPLETE,
        athome_key=card.athome_key,
        url=url,
        title=card.title,
        address=location,
        station=_string(first_traffic.get("stationName")) or _station_from_location(location),
        walk_minutes=_number(first_traffic.get("tohoJikan")) or _walk_from_location(location),
        building_type=_string(raw.get("type")),
        floors=_string(info.get("kaidateKai")),
        construction_date=_strip_html(_string(info.get("chikunengetsu"))),
        price=PriceBreakdown(
            rent=_yen(contract.get("price")),
            management_fee=_yen(contract.get("managementFee")),
            deposit=_optional_yen(contract.get("deposit")),
            key_money=_optional_yen(contract.get("keyMoney")),
            deposit_raw=_string(contract.get("deposit")),
            key_money_raw=_string(contract.get("keyMoney")),
        ),
        floor_plan=_string(info.get("madoriTypeNm")),
        area_m2=_number(area_info.get("area")) or 0.0,
        usp_tags=_flags(raw),
        photo_urls=photos,
        agency_reference=_string(_dict(raw.get("kaiin")).get("syogo")),
        source_data=raw,
    )


def ingest_recommendation_cards(
    cards: list[RecommendationCard], store: BaseDataStore
) -> RecommendationIngestionResult:
    """Upsert cards as complete summaries and return hydration metadata."""
    unique: dict[str, ListingSummary] = {}
    for card in cards:
        if card.athome_key not in unique:
            unique[card.athome_key] = normalize_recommendation_card(card)
    intents: list[HydrationIntent] = []
    for summary in unique.values():
        internal_id = store.upsert_listing(summary)
        intents.append(
            HydrationIntent(
                athome_key=summary.athome_key, internal_id=internal_id, url=summary.url
            )
        )
    return RecommendationIngestionResult(list(unique.values()), intents)


def _dict(value: object) -> dict[str, Any]:
    """Return a dictionary value or an empty mapping."""
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    """Return non-empty text, normalizing absent card fields to None."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _number(value: object) -> float | None:
    """Extract the first non-negative number from a card field."""
    match = _NUMBER.search(str(value)) if value is not None else None
    return float(match.group(1)) if match else None


def _yen(value: object) -> int:
    """Convert yen or 万円 text into yen without failing optional cards."""
    text = str(value or "")
    number = _number(text.replace(",", "")) or 0
    return round(number * 10_000) if "万" in text else round(number)


def _optional_yen(value: object) -> int | None:
    """Convert numeric upfront terms while retaining raw month terms."""
    text = str(value or "")
    return _yen(text) if "円" in text else None


def _absolute_image(value: object) -> str:
    """Build a stable public image URL from an AtHome path."""
    text = str(value)
    return text if text.startswith("http") else f"{_BASE_URL}{text}"


def _station_from_location(value: str) -> str | None:
    """Extract a station name from displayed location text."""
    match = re.search(r"「?([^「」/]+?)」?駅", value)
    return match.group(1).strip() if match else None


def _walk_from_location(value: str) -> float | None:
    """Extract walking minutes from displayed location text."""
    match = re.search(r"徒歩\s*([0-9]+)分", value)
    return float(match.group(1)) if match else None


def _strip_html(value: str | None) -> str | None:
    """Remove the simple line-break markup used in card construction dates."""
    return value.replace("<br>", " ") if value else None


def _flags(raw: dict[str, Any]) -> list[str]:
    """Expose affirmative card flags as stable summary tags."""
    flags = _dict(raw.get("isOption"))
    labels = {"isNewMark": "new", "isMadoriFigure": "floor-plan", "hasPhotos": "photos"}
    return [label for key, label in labels.items() if flags.get(key) is True]
