"""Validated extraction of AtHome's embedded ``serverApp-state`` detail payload."""

from __future__ import annotations

import json
from typing import Any

from selectolax.parser import HTMLParser


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
