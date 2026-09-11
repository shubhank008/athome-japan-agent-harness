"""Parser for AtHome rental/purchase list (results) pages (M3 T15).

Turns one captured AtHome results page into a list of :class:`ListingSummary`
objects. A page is made of :class:`BuildingBlock` entries: each has a heading
(``.p-property--building``) carrying the shared building identity (title,
address, transport, building type) and one or more unit sub-blocks
(``.p-property__room--detailbox``) carrying per-room data (floor, rent,
management fee, deposit/key money, floor plan, area, facilities, photos).

Multi-unit buildings therefore yield one summary per unit that all share the
building identity. Detached houses may have no room number: a missing optional
cell produces a parse warning instead of aborting the page, so a single broken
sub-block never drops the whole listing. Disabled-facility markers become
``probable_negatives`` (plausibly absent features) while the enabled facility
items become ``usp_tags``.

Parsing is pure DOM work over a selectolax tree; no network I/O happens here.
This module is allowed to import selectolax because it sits at the parser
boundary, matching the project's Abstract First convention (third-party parsing
libraries live only in the scraping adapter/parser tier).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser, Node

from athome_harness.models import ListingSummary, PriceBreakdown
from athome_harness.scraping.age import age_values

logger = logging.getLogger(__name__)

# Structural CSS selectors for the current AtHome list-page DOM.
_BUILDING = "div.p-property--building"
_DETAILBOX = "div.p-property__room--detailbox"
_CURRENT_CARD = "div.property-card"
_TITLE = "h2.p-property__title--building"
_HINT_DD = "dl.p-property__information-hint dd"
_ROOM_NUMBER = "li.p-property__room-number"
_RENT = "b.p-property__information-rent"
_PRICE = "p.p-property__information-price"
_KEYMONEY = "li.p-property__room-keymoney"
_FLOORPLAN = "li.p-property__room-floorplan"
_FLOOR = "div.p-property__floor"
_FACILITY_LIST = "div.p-property__information-facility"

# The single class that marks a facility as disabled (probable negative).
_DISABLED_FACILITY = "p-property__information-facility_disabled-list"

_BASE_URL = "https://www.athome.co.jp"

# Regexes for the small set of numeric units encountered on real list pages.
_RE_MAN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*万円")
_RE_YEN = re.compile(r"([0-9,]+)\s*円")
_RE_WALK = re.compile(r"徒歩([0-9]+)分")
_RE_STATION = re.compile(r"「([^」]+)」駅")
# Building-hint tokens that terminate the property-type label: a floor count
# (``2階建``), a construction date (``2026年6月``), or a parenthesised age note.
_RE_FLOOR_COUNT = re.compile(r"^[0-9]+階建")
# Build-date form ``YYYY年M月`` inside the third hint (matched before the floor
# count so a construction year is never mistaken for the building-type label).
_RE_BUILD_DATE = re.compile(r"([0-9]{4})年([0-9]{1,2})月")
_DAYS_PER_YEAR = 365.25


def _age_metadata(
    building_info: str, ref_date: date
) -> tuple[float | None, str | None, str | None, str | None]:
    """Return rounded years, raw age, construction date, and display text."""
    match = _RE_BUILD_DATE.search(building_info)
    if not match:
        logger.warning("list_parser: no construction date in building hint %r", building_info)
        return None, building_info or None, None, None
    build_year, build_month = int(match.group(1)), int(match.group(2))
    build_date = date(build_year, build_month, 1)
    years, age_raw, display = age_values(match.group(0), ref_date, build_date)
    return years, age_raw, match.group(0), display


@dataclass
class BuildingBlock:
    """One parsed building heading plus its unit sub-blocks (T15)."""

    title: str
    address: str
    station: str | None
    walk_minutes: float | None
    building_type: str | None
    age: float | None
    age_raw: str | None = None
    construction_date: str | None = None
    age_display: str | None = None
    units: list[Node] = field(default_factory=list)


def parse_list_page(html: str, ref_date: date | None = None) -> list[ListingSummary]:
    """Parse an AtHome results page into one :class:`ListingSummary` per unit.

    ``ref_date`` is the frame used to convert a ``YYYY年M月`` construction date
    (from the third building hint) into an age in years; it defaults to today.
    Tests pass a fixed date so the age is deterministic; production callers rely
    on the default.

    Raises nothing for missing optional fields: missing cells are logged as
    parse warnings and the affected unit is still emitted. Only structurally
    invalid input (no units at all) is treated as an empty result.
    """
    tree = HTMLParser(html)
    summaries: list[ListingSummary] = []
    buildings = tree.css(_BUILDING)
    if buildings:
        for building in buildings:
            block = _parse_building_heading(building, ref_date or date.today())
            if not block.units:
                logger.warning("list_parser: building %r has no unit sub-blocks", block.title)
                continue
            for unit in block.units:
                summary = _parse_unit(unit, block)
                if summary is not None:
                    summaries.append(summary)
        return summaries
    return _parse_current_cards(tree, ref_date or date.today())


def _parse_current_cards(tree: HTMLParser, ref_date: date) -> list[ListingSummary]:
    """Parse AtHome's current ``property-card`` list-page markup."""
    summaries: list[ListingSummary] = []
    for card in tree.css(_CURRENT_CARD):
        title_node = card.css_first("h2.property-title")
        location_node = card.css_first("li.info-item--location")
        station_node = card.css_first("li.info-item--station")
        type_node = card.css_first("li.info-item--type")
        title = title_node.text(strip=True) if title_node else ""
        address = location_node.text(strip=True) if location_node else ""
        transport = station_node.text(separator=" ", strip=True) if station_node else ""
        building_info = type_node.text(separator=" ", strip=True) if type_node else ""
        building_type = _extract_building_type(building_info)
        age, age_raw, construction_date, age_display = _age_metadata(building_info, ref_date)
        station = _extract_station(transport)
        walk = _extract_walk_minutes(transport)
        for room in card.css("div.room-info-section"):
            summary = _parse_current_room(
                room,
                title=title,
                address=address,
                station=station,
                walk_minutes=walk,
                building_type=building_type,
                age=age,
                age_raw=age_raw,
                construction_date=construction_date,
                age_display=age_display,
            )
            if summary is not None:
                summaries.append(summary)
    return summaries


def _parse_current_room(
    room: Node,
    *,
    title: str,
    address: str,
    station: str | None,
    walk_minutes: float | None,
    building_type: str | None,
    age: float | None,
    age_raw: str | None,
    construction_date: str | None,
    age_display: str | None,
) -> ListingSummary | None:
    """Parse one room row from a current ``property-card`` element."""
    link = room.css_first("a[href*='/chintai/']")
    if link is None:
        return None
    url = _absolute_url(link.attributes.get("href"))
    if url is None:
        return None
    match = re.search(r"/chintai/([^/?#]+)", urlsplit(url).path)
    if match is None:
        return None
    room_id = match.group(1)
    room_node = room.css_first("li.room-number")
    price_node = room.css_first("li.price")
    fee_node = room.css_first("li.fees")
    layout_node = room.css_first("li.layout-size")
    rent_node = room.css_first(".rent-value")
    rent_raw = rent_node.text(strip=True) if rent_node else ""
    price_values = _room_texts(price_node)
    fee_values = _room_texts(fee_node)
    layout_values = _room_texts(layout_node)
    rent = _parse_man_yen(rent_raw)
    management_fee = _parse_yen_text(price_values[1]) if len(price_values) > 1 else 0
    deposit_raw = fee_values[0] if fee_values else None
    key_money_raw = fee_values[1] if len(fee_values) > 1 else None
    deposit = _parse_optional_current_term(deposit_raw)
    key_money = _parse_optional_current_term(key_money_raw)
    floor_plan = layout_values[0] if layout_values else None
    area = _parse_area_m2(layout_values[1]) if len(layout_values) > 1 else 0.0
    photos = _extract_photos(room)
    return ListingSummary(
        internal_id=room_id,
        athome_key=room_id,
        url=f"{_BASE_URL}/chintai/{room_id}/",
        title=title,
        address=address,
        station=station,
        walk_minutes=walk_minutes,
        building_type=building_type,
        floors=room_node.text(strip=True) if room_node else None,
        age=age,
        age_raw=age_raw,
        construction_date=construction_date,
        age_display=age_display,
        price=PriceBreakdown(
            rent=rent,
            management_fee=management_fee,
            deposit=deposit,
            key_money=key_money,
            deposit_raw=deposit_raw,
            key_money_raw=key_money_raw,
        ),
        floor_plan=floor_plan,
        area_m2=area,
        photo_urls=photos,
    )


def _room_texts(node: Node | None) -> list[str]:
    """Return direct room-info text values without nested rent fragments."""
    return [child.text(strip=True) for child in node.css(".room-info-item__text")] if node else []


def _parse_yen_text(raw: str) -> int:
    """Parse a current-card yen amount, returning zero when absent."""
    match = _RE_YEN.search(raw)
    return _to_int(match.group(1)) if match else 0


def _parse_optional_current_term(raw: str | None) -> int | None:
    """Parse numeric terms while preserving month/day terms as raw-only."""
    if not raw:
        return None
    if raw == "なし":
        return 0
    if "万円" in raw:
        return _parse_man_yen(raw)
    if "円" in raw:
        return _parse_yen_text(raw)
    return None


def _parse_building_heading(building: Node, ref_date: date) -> BuildingBlock:
    """Extract the shared building identity and the unit sub-block nodes."""
    title_node = building.css_first(_TITLE)
    title = title_node.text(strip=True) if title_node else ""

    hints = building.css(_HINT_DD)
    address = hints[0].text(strip=True) if hints else ""
    transport = hints[1].text(separator=" ", strip=True) if len(hints) >= 2 else ""
    building_info = hints[2].text(separator=" ", strip=True) if len(hints) >= 3 else ""

    station = _extract_station(transport)
    walk = _extract_walk_minutes(transport)
    building_type = _extract_building_type(building_info)
    age, age_raw, construction_date, age_display = _age_metadata(building_info, ref_date)

    return BuildingBlock(
        title=title,
        address=address,
        station=station,
        walk_minutes=walk,
        building_type=building_type,
        age=age,
        age_raw=age_raw,
        construction_date=construction_date,
        age_display=age_display,
        units=building.css(_DETAILBOX),
    )


def _extract_station(transport: str) -> str | None:
    """Return the nearest station name from ``「駅名」`` or ``None``."""
    match = _RE_STATION.search(transport)
    return match.group(1) if match else None


def _extract_walk_minutes(transport: str) -> float | None:
    """Return the minutes on foot, or ``None`` when transit is bus-only."""
    match = _RE_WALK.search(transport)
    return float(match.group(1)) if match else None


def _extract_building_type(building_info: str) -> str | None:
    """Return only the property-type label from the building info hint.

    The hint renders like ``賃貸アパート 2階建 2026年6月`` (type, floor count,
    construction date, occasionally a parenthesised age note), so the label is
    the leading tokens up to the first floor-count or date token. Floor count
    and construction date are excluded per the DOM access map.
    """
    type_tokens: list[str] = []
    for token in building_info.split():
        if _RE_FLOOR_COUNT.match(token) or _RE_BUILD_DATE.match(token) or token.startswith("("):
            break
        type_tokens.append(token)
    label = " ".join(type_tokens).strip()
    return label or None


def _parse_unit(unit: Node, block: BuildingBlock) -> ListingSummary | None:
    """Parse one room sub-block into a :class:`ListingSummary`.

    Returns ``None`` only when the unit carries no usable ``data-bukken-no``;
    missing optional fields log a warning and are kept as ``None``/empty.
    """
    athome_key = unit.attributes.get("data-bukken-no")
    if not athome_key:
        logger.warning("list_parser: unit in %r has no data-bukken-no; skipped", block.title)
        return None

    floors = _extract_room_number(unit, block)
    price = _extract_price(unit, block)
    floor_plan, area = _extract_floor_plan(unit, block)
    usp_tags, probable_negatives = _extract_facilities(unit, block)
    photo_urls = _extract_photos(unit)

    return ListingSummary(
        internal_id=athome_key,
        athome_key=athome_key,
        url=f"{_BASE_URL}/chintai/{athome_key}/",
        title=block.title,
        address=block.address,
        station=block.station,
        walk_minutes=block.walk_minutes,
        building_type=block.building_type,
        floors=floors,
        age=block.age,
        age_raw=block.age_raw,
        construction_date=block.construction_date,
        age_display=block.age_display,
        price=price,
        floor_plan=floor_plan,
        area_m2=area,
        usp_tags=usp_tags,
        probable_negatives=probable_negatives,
        photo_urls=photo_urls,
    )


def _extract_room_number(unit: Node, block: BuildingBlock) -> str | None:
    """Return the room's floor/number text, warning when it is absent.

    Detached houses may have no room number; this is a valid (optional) state,
    so it logs a warning rather than failing the parse.
    """
    node = unit.css_first(_ROOM_NUMBER)
    text = node.text(strip=True) if node else ""
    if not text:
        logger.warning("list_parser: unit %r has no room number", block.title)
        return None
    return text


def _extract_price(unit: Node, block: BuildingBlock) -> PriceBreakdown:
    """Parse rent, management fee, deposit, and key money for one unit.

    Deposit/key-money cells keep their raw displayed term (``なし``, ``5万円``,
    ``1ヶ月``, ...) alongside the numeric yen value so a month-based term is never
    indistinguishable from zero.
    """
    rent = 0
    management_fee = 0
    deposit = 0
    key_money = 0
    deposit_raw = key_money_raw = None

    rent_node = unit.css_first(_RENT)
    if rent_node:
        rent = _parse_man_yen(rent_node.text(strip=True))
    price_node = unit.css_first(_PRICE)
    if price_node is not None:
        management_fee = _parse_management_fee(price_node.text(separator="", strip=True))

    deposit, key_money, deposit_raw, key_money_raw = _parse_deposit_key_money(unit, block)
    return PriceBreakdown(
        rent=rent,
        management_fee=management_fee,
        deposit=deposit,
        key_money=key_money,
        deposit_raw=deposit_raw,
        key_money_raw=key_money_raw,
    )


def _parse_management_fee(raw: str) -> int:
    """Extract the management fee (円) embedded after the rent value."""
    # The price node renders like ``6.8万円 4,000円``; grab the 円 amount.
    match = _RE_YEN.search(raw)
    if not match:
        return 0
    return _to_int(match.group(1))


def _parse_deposit_key_money(
    unit: Node, block: BuildingBlock
) -> tuple[int, int, str | None, str | None]:
    """Return ``(deposit, key_money, deposit_raw, key_money_raw)``.

    Yen amounts parse through the optional-numeric path so ``なし`` yields zero,
    while the raw displayed term (``なし``, ``1ヶ月``, ``5万円``) is preserved so a
    month-based deposit/key money is never indistinguishable from zero.
    """
    node = unit.css_first(_KEYMONEY)
    if node is None:
        return (0, 0, None, None)
    texts = [p.text(strip=True) for p in node.css("p")]
    texts += [s.text(strip=True) for s in node.css("span")]
    deposit_text = texts[0] if texts else ""
    key_money_text = texts[1] if len(texts) > 1 else ""
    deposit = _parse_optional_yen(deposit_text, "deposit", block)
    key_money = _parse_optional_yen(key_money_text, "key money", block)
    return (deposit, key_money, deposit_text or None, key_money_text or None)


def _parse_optional_yen(raw: str, label: str, block: BuildingBlock) -> int:
    """Convert a deposit/key-money cell to yen, 0 for ``なし``, warning otherwise."""
    if not raw or raw == "なし":
        return 0
    if "万円" in raw:
        return _parse_man_yen(raw)
    if "円" in raw:
        match = _RE_YEN.search(raw)
        return _to_int(match.group(1)) if match else 0
    logger.warning(
        "list_parser: %s %r in %r not convertible; recorded as 0", label, raw, block.title
    )
    return 0


def _parse_man_yen(raw: str) -> int:
    """Convert a ``X万円`` amount to yen (e.g. ``6.8`` -> 68000).

    The rent cell on a list page holds only the bare number (e.g. ``6.8``) with
    the 万円 unit placed outside the ``<b>``; the deposit/key-money cells carry
    the unit inline. Both forms are handled here.
    """
    match = _RE_MAN.search(raw)
    if match:
        return round(float(match.group(1)) * 10_000)
    # Bare decimal in 万円 units (list-page rent cell).
    number = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)", raw.strip())
    if number:
        return round(float(number.group(1)) * 10_000)
    return 0


def _to_int(comma_sep: str) -> int:
    """Parse a possibly comma-separated integer string."""
    return int(comma_sep.replace(",", ""))


def _extract_floor_plan(unit: Node, block: BuildingBlock) -> tuple[str | None, float]:
    """Return ``(floor_plan, area_m2)`` for one unit.

    Floor plan is optional; its absence logs a warning. Area defaults to 0 when
    unparsable so the page parse never aborts.
    """
    floor_plan = None
    area = 0.0
    floor_node = unit.css_first(_FLOOR)
    if floor_node:
        floor_plan = floor_node.text(strip=True)
    else:
        logger.warning("list_parser: unit %r has no floor plan", block.title)

    fp_list = unit.css_first(_FLOORPLAN)
    if fp_list is not None:
        spans = fp_list.css("span")
        if spans:
            area = _parse_area_m2(spans[-1].text(strip=True))
    return (floor_plan, area)


def _parse_area_m2(raw: str) -> float:
    """Parse an area like ``28.98m²`` to a float, 0.0 when unparsable."""
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw)
    if not match:
        logger.warning("list_parser: unparsable area %r", raw)
        return 0.0
    return float(match.group(1))


def _extract_facilities(unit: Node, block: BuildingBlock) -> tuple[list[str], list[str]]:
    """Split facility items into USP tags (enabled) and probable negatives (disabled).

    Disabled items carry the ``p-property__information-facility_disabled-list``
    class; all other items under the facility list are treated as confirmed USPs.
    """
    usp_tags: list[str] = []
    probable_negatives: list[str] = []
    fac_list = unit.css_first(_FACILITY_LIST)
    if fac_list is None:
        return (usp_tags, probable_negatives)
    for item in fac_list.css("li"):
        text = item.text(strip=True)
        if not text:
            continue
        classes = item.attributes.get("class") or ""
        if _DISABLED_FACILITY in classes:
            probable_negatives.append(text)
        else:
            usp_tags.append(text)
    return (usp_tags, probable_negatives)


def _absolute_url(url: str | None) -> str | None:
    """Return ``url`` as an absolute URL, prepending the base for ``/`` paths."""
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{_BASE_URL}{url}"
    return None


def _extract_photos(unit: Node) -> list[str]:
    """Return absolute photo URLs from the unit's image elements."""
    urls: list[str] = []
    for img in unit.css("img"):
        src = _absolute_url(img.attributes.get("src") or img.attributes.get("data-original"))
        if src:
            urls.append(src)
    return urls
