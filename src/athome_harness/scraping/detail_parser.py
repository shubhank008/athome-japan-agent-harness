"""Parser for a single AtHome listing detail page (M3 T16).

Turns one captured AtHome detail page into a :class:`ListingDetail`, which
extends :class:`ListingSummary` with the full free-text description, the complete
photo set, the dedicated floor-plan image URL, and the extra facility features.

The page is made of several `table.dataTbl` blocks: a property-data table
(間取り, 面積, 築年月, 種目, 階建 / 階, 住所, 交通, ...), cost tables (賃料,
管理費等, 敷金, 礼金, ...), and facility-feature tables (バス・トイレ, キッチン,
セキュリティー, 収納, 設備・サービス, TV・通信, その他, ...). A dedicated
``paymentInfo.typeChintai`` block carries the canonical price. The photo strip
``#detail-image_view ul.zoomList`` carries every image with a ``subCategory``
label, so the floor-plan image is the one labelled 間取図 (floor plan). The USP
section ``div.pointList`` carries free-text highlights and icon tags.

Disabled-facility markers (``facility_disabled-list``) contribute
``probable_negatives``; absent optional cells produce parse warnings rather than
aborting the page.
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser, Node

from athome_harness.models import ListingDetail, PriceBreakdown

logger = logging.getLogger(__name__)

# Structural CSS selectors for the current AtHome detail-page DOM.
_PAYMENT_INFO = "div.paymentInfo.typeChintai"
_DATA_TBL = "table.dataTbl"
_PHOTO_STRIP = "#detail-image_view ul.zoomList li.item"
_PHOTO_NAME = "dt#subCategory"
_POINT_DD = "#item-detai_basic__point dd"
_POINT_ICONS = "div.pointList ul.typeInline li img"

_BASE_URL = "https://www.athome.co.jp"

_RE_MAN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*万円")
_RE_YEN = re.compile(r"([0-9,]+)\s*円")
_RE_WALK = re.compile(r"徒歩([0-9]+)分")
# Matches a station name immediately before 駅, with or without 「」 quotes.
_RE_STATION = re.compile(r"「?([^/「」\s]{1,24})」?駅")
_RE_AREA = re.compile(r"([0-9]+(?:\.[0-9]+)?)")

_MAP_LINK_SUFFIX = "地図で見る"

# Facility-category <th> labels whose <td> items count as facility features.
_FACILITY_CATEGORIES = frozenset(
    {
        "バス・トイレ",
        "キッチン",
        "セキュリティー",
        "収納",
        "設備・サービス",
        "TV・通信",
        "その他",
        "室内設備",
    }
)

# DataTbl <th> labels (in priority order) that map to each detail attribute.
TITLE_LABELS = ("建物名・部屋番号",)
ADDRESS_LABELS = ("住所", "所在地")
TRANSPORT_LABELS = ("交通",)
BUILDING_TYPE_LABELS = ("物件種目", "種目")
FLOOR_PLAN_LABELS = ("間取り",)
AREA_LABELS = ("専有面積", "面積")
FLOORS_LABELS = ("階建 / 階",)
DESCRIPTION_LABELS = ("備考",)


def _field(fields: dict[str, str], labels: tuple[str, ...]) -> str | None:
    """Return the first present value among ``labels``, or ``None``."""
    for label in labels:
        if label in fields:
            return fields[label]
    return None


def parse_detail_page(html: str) -> ListingDetail:
    """Parse an AtHome detail page into a :class:`ListingDetail`.

    Required identity fields (athome_key) come from the page title/URL; optional
    cells that are absent are logged as warnings and left as ``None``/empty.
    """
    tree = HTMLParser(html)
    athome_key = _extract_key(tree, html)
    fields = _extract_data_fields(tree)
    price = _extract_price(tree)
    photos = _extract_photos(tree)
    floor_plan_image = _extract_floor_plan_image(tree)
    point_text, point_icons = _extract_usp(tree)
    facility_features, probable_negatives = _extract_facilities(tree)

    usp_tags = point_icons if point_icons else ([point_text] if point_text else [])
    title = _field(fields, TITLE_LABELS) or ""
    address = (_field(fields, ADDRESS_LABELS) or "").removesuffix(_MAP_LINK_SUFFIX)
    station, walk = _parse_transport(_field(fields, TRANSPORT_LABELS) or "")

    return ListingDetail(
        internal_id=athome_key,
        athome_key=athome_key,
        url=f"{_BASE_URL}/chintai/{athome_key}/",
        title=title or "",
        address=address or "",
        station=station,
        walk_minutes=walk,
        building_type=_field(fields, BUILDING_TYPE_LABELS),
        floors=_field(fields, FLOORS_LABELS),
        age=None,
        price=price,
        floor_plan=_field(fields, FLOOR_PLAN_LABELS),
        area_m2=_parse_area(_field(fields, AREA_LABELS)),
        usp_tags=usp_tags,
        probable_negatives=probable_negatives,
        photo_urls=photos,
        description=_field(fields, DESCRIPTION_LABELS) or "",
        floor_plan_image_url=floor_plan_image,
        facility_features=facility_features,
    )


def _extract_key(tree: HTMLParser, html: str) -> str:
    """Return the listing key from the page ``<title>`` ``[N]`` bracket.

    Falls back to ``unknown`` (logged) when the title does not carry the key.
    """
    title_node = tree.css_first("title")
    if title_node is None:
        logger.warning("detail_parser: page has no <title>; key unknown")
        return "unknown"
    match = re.search(r"\[([0-9]+)\]", title_node.text(strip=True))
    if not match:
        logger.warning("detail_parser: cannot find listing key in <title>")
        return "unknown"
    return match.group(1)


def _extract_data_fields(tree: HTMLParser) -> dict[str, str]:
    """Collect the first value for every label seen across all data tables.

    Iterating every `table.dataTbl` covers the property-data, cost, and
    facility-feature tables uniformly; the first occurrence of each label wins.
    """
    fields: dict[str, str] = {}
    for table in tree.css(_DATA_TBL):
        for tr in table.css("tr"):
            ths = tr.css("th")
            tds = tr.css("td")
            for th, td in zip(ths, tds, strict=False):
                label = th.text(strip=True)
                value = td.text(separator="", strip=True)
                if label and label not in fields:
                    fields[label] = value
    return fields


def _extract_price(tree: HTMLParser) -> PriceBreakdown:
    """Parse rent, management fee, deposit, and key money from the price block."""
    rent = management_fee = deposit = key_money = 0
    payment = tree.css_first(_PAYMENT_INFO)
    if payment is not None:
        for dl in payment.css("dl.data"):
            dt = dl.css_first("dt")
            dd = dl.css_first("dd")
            if dt is None or dd is None:
                continue
            label = dt.text(strip=True)
            value = dd.text(strip=True)
            if "賃料" in label:
                rent = _parse_man_yen(value)
            elif "管理費" in label:
                management_fee = _parse_yen(value)
            elif "敷金" in label:
                deposit = _parse_optional_yen(value, "deposit")
            elif "礼金" in label:
                key_money = _parse_optional_yen(value, "key money")
    return PriceBreakdown(
        rent=rent,
        management_fee=management_fee,
        deposit=deposit,
        key_money=key_money,
    )


def _extract_photos(tree: HTMLParser) -> list[str]:
    """Return absolute URLs of every photo in the detail photo strip."""
    urls: list[str] = []
    for item in tree.css(_PHOTO_STRIP):
        img = item.css_first("img")
        if img is None:
            continue
        src = _absolute_url(
            img.attributes.get("src") or img.attributes.get("data-original")
        )
        if src:
            urls.append(src)
    return urls


def _extract_floor_plan_image(tree: HTMLParser) -> str | None:
    """Return the photo labelled 間取図 (floor plan), or ``None`` if absent."""
    for item in tree.css(_PHOTO_STRIP):
        name = item.css_first(_PHOTO_NAME)
        if name is None or name.text(strip=True) != "間取図":
            continue
        img = item.css_first("img")
        if img is None:
            continue
        return _absolute_url(
            img.attributes.get("src") or img.attributes.get("data-original")
        )
    return None


def _absolute_url(url: str | None) -> str | None:
    """Return ``url`` as an absolute URL, prepending the base for ``/`` paths."""
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{_BASE_URL}{url}"
    return None


def _extract_usp(tree: HTMLParser) -> tuple[str, list[str]]:
    """Return ``(point_text, icon_tags)`` from the USP section.

    ``point_text`` is the free-text highlight line; ``icon_tags`` are the
    alt-text labels of the icon strip. When icons exist they take precedence as
    ``usp_tags``; otherwise the free-text line is used.
    """
    point_text = ""
    point_dd = tree.css_first(_POINT_DD)
    if point_dd is not None:
        point_text = point_dd.text(separator="", strip=True)
    icons = [
        alt
        for img in tree.css(_POINT_ICONS)
        if (alt := img.attributes.get("alt"))
    ]
    return (point_text, icons)


def _extract_facilities(tree: HTMLParser) -> tuple[list[str], list[str]]:
    """Return ``(facility_features, probable_negatives)`` from facility rows.

    Only facility-category rows (バス・トイレ, キッチン, セキュリティー, ...)
    contribute items; cost and agency-data rows are ignored. Items whose element
    carries the ``facility_disabled-list`` class become probable negatives, the
    rest (split on the Japanese comma) become confirmed facility features.
    """
    features: list[str] = []
    negatives: list[str] = []
    for table in tree.css(_DATA_TBL):
        for tr in table.css("tr"):
            th = tr.css_first("th")
            if th is None or th.text(strip=True) not in _FACILITY_CATEGORIES:
                continue
            for td in tr.css("td"):
                classes = td.attributes.get("class") or ""
                items = td.css("p, span, li") or [td]
                for item in items:
                    text = item.text(strip=True)
                    if not text:
                        continue
                    if _is_disabled(item, classes):
                        negatives.append(text)
                        continue
                    features.extend(_split_features(text))
    return (_dedupe(features), _dedupe(negatives))


def _split_features(text: str) -> list[str]:
    """Split a facility cell on the Japanese comma and trim whitespace."""
    return [piece.strip() for piece in text.split("、") if piece.strip()]


def _is_disabled(node: Node, td_classes: str) -> bool:
    """True when a facility item is marked disabled (probable negative)."""
    node_classes = node.attributes.get("class") or ""
    return "facility_disabled-list" in (td_classes + " " + node_classes)


def _dedupe(items: list[str]) -> list[str]:
    """Return items in order, dropping duplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _parse_transport(raw: str) -> tuple[str | None, float | None]:
    """Split ``「駅名」 / 駅 徒歩N分`` into ``(station, walk_minutes)``."""
    station = None
    walk: float | None = None
    match = _RE_STATION.search(raw)
    if match:
        station = match.group(1)
    walk_match = _RE_WALK.search(raw)
    if walk_match:
        walk = float(walk_match.group(1))
    return (station, walk)


def _parse_man_yen(raw: str) -> int:
    """Convert ``X万円`` to yen (e.g. ``5.58`` -> 55800)."""
    match = _RE_MAN.search(raw)
    if not match:
        return 0
    return round(float(match.group(1)) * 10_000)


def _parse_yen(raw: str) -> int:
    """Convert a ``円`` amount to yen, 0 for ``なし``/missing."""
    if not raw or raw == "なし":
        return 0
    match = _RE_YEN.search(raw)
    if not match:
        logger.warning("detail_parser: unparsable yen value %r", raw)
        return 0
    return int(match.group(1).replace(",", ""))


def _parse_optional_yen(raw: str, label: str) -> int:
    """Convert a deposit/key-money cell to yen, warning on non-convertible."""
    if not raw or raw == "なし":
        return 0
    if "万円" in raw:
        return _parse_man_yen(raw)
    if "円" in raw:
        return _parse_yen(raw)
    logger.warning("detail_parser: %s %r not convertible; recorded as 0", label, raw)
    return 0


def _parse_area(raw: str | None) -> float:
    """Parse an area string like ``24.99m²`` to a float, 0.0 when unparsable."""
    if not raw:
        return 0.0
    match = _RE_AREA.search(raw)
    if not match:
        logger.warning("detail_parser: unparsable area %r", raw)
        return 0.0
    return float(match.group(1))
