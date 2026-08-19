"""Unit tests for the AtHome list-page parser (M3 T15).

The primary cases exercise the real captured Osaka rental page under
``tests/fixtures/`` (genuine live data, not synthetic). A detached-house
(no-room-number) edge case is covered by a minimal synthetic DOM tree that is
explicitly labelled synthetic; that data does not appear in the live capture and
exists only to prove missing-optional-field handling (FR-8).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from athome_harness.scraping.list_parser import parse_list_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LIST_FIXTURE = FIXTURES / "osaka_rental_list.html"


def _load_list() -> str:
    return LIST_FIXTURE.read_text(encoding="utf-8")


def test_captured_list_parses_into_summaries() -> None:
    """The live Osaka list page yields one summary per unit sub-block."""
    summaries = parse_list_page(_load_list())
    # 30 building blocks / 460 unit boxes confirmed on the captured page.
    assert len(summaries) == 460


def test_captured_list_summaries_are_unique() -> None:
    """Every unit summary carries a distinct internal/athome key."""
    summaries = parse_list_page(_load_list())
    keys = [s.internal_id for s in summaries]
    assert len(keys) == len(set(keys))
    assert all(s.athome_key == s.internal_id for s in summaries)


def test_multi_unit_building_shares_identity() -> None:
    """Units of one building share title/address/station but differ in room data."""
    summaries = parse_list_page(_load_list())
    grouped: dict[str, list] = {}
    for s in summaries:
        grouped.setdefault(s.title, []).append(s)
    multi = next(units for units in grouped.values() if len(units) >= 2)
    base = multi[0]
    for unit in multi[1:]:
        assert unit.title == base.title
        assert unit.address == base.address
        assert unit.station == base.station
        assert unit.building_type == base.building_type
    # Different rooms in the same building have distinct keys.
    assert len({u.internal_id for u in multi}) == len(multi)


def test_captured_unit_scalar_fields() -> None:
    """Rent, management fee, floor plan, and area parse from the first unit."""
    summaries = parse_list_page(_load_list())
    first = summaries[0]
    assert first.price.rent == 68_000
    assert first.price.management_fee == 4_000
    assert first.floors == "1階"
    assert first.floor_plan == "1LDK"
    assert first.area_m2 == pytest.approx(28.98)


def test_captured_facilities_split_into_usp_and_negatives() -> None:
    """Enabled facility items become USP tags, disabled ones probable negatives."""
    summaries = parse_list_page(_load_list())
    first = summaries[0]
    assert any("駐車場" in tag for tag in first.usp_tags)
    assert any("ペット相談" in tag for tag in first.probable_negatives)
    total_neg = sum(len(s.probable_negatives) for s in summaries)
    # 733 disabled markers are present across the captured list page.
    assert total_neg >= 700


def test_captured_unit_has_detail_url_and_photo() -> None:
    """Each unit exposes a canonical detail URL built from its key."""
    summaries = parse_list_page(_load_list())
    first = summaries[0]
    assert first.url == f"https://www.athome.co.jp/chintai/{first.athome_key}/"
    assert len(first.photo_urls) >= 1


def test_captured_building_type_excludes_floor_and_date() -> None:
    """The building type holds only the property type, not floor/construction data.

    The third building hint renders like ``賃貸アパート 2階建 2026年6月``; only the
    leading type label must be kept (DOM access map, T15).
    """
    summaries = parse_list_page(_load_list())
    types = {s.building_type for s in summaries}
    # Both captured property kinds survive as bare type labels.
    assert "賃貸アパート" in types
    assert "賃貸マンション" in types
    for kind in types:
        assert "階建" not in kind
        assert not any(ch.isdigit() for ch in kind)


def test_captured_building_age_is_not_silently_dropped() -> None:
    """The third hint's ``2026年8月`` construction date yields a real age value.

    The first captured building renders ``賃貸アパート 3階建 2026年8月``; the observed
    date must be converted into a non-None age (regression: FR-9 and the DOM
    access map require that an observed age is never silently defaulted to None).
    The fixed ref_date makes the value deterministic.
    """
    summaries = parse_list_page(_load_list(), ref_date=date(2026, 8, 1))
    first = summaries[0]
    # Built 2026年8月, so at a 2026-08-01 ref_date the age is ~0 years (new build).
    assert first.age == pytest.approx(0.0)
    # The observed construction date is never silently dropped: every one of the
    # 460 units (sharing 30 building blocks, all with a construction date) has a
    # real age value.
    assert all(s.age is not None for s in summaries)
    # The captured 2002+ build and a 1991 build yield distinct, non-zero ages.
    older = max(s.age for s in summaries)
    assert older > 30.0


def test_captured_month_based_key_money_preserves_raw_term() -> None:
    """A ``1ヶ月`` key-money term is recorded as 0 yen but kept as raw text.

    Without the raw term, an 85-unit month-based capture would be
    indistinguishable from ``なし``/zero. The raw field must disambiguate.
    """
    summaries = parse_list_page(_load_list())
    month_units = [
        s for s in summaries if s.price.key_money_raw == "1ヶ月"
    ]
    assert month_units
    for unit in month_units:
        assert unit.price.key_money == 0
        # Distinguishable from a genuinely absent/なし key money.
        assert unit.price.key_money_raw == "1ヶ月"


# Synthetic edge cases. This DOM is hand-built (not from a live capture) purely
# to exercise the missing-optional-field path: a detached house has no room
# number, and rent/key-money cells are present while the management-fee span and
# facility list are absent.
SYNTHETIC_DETACHED_HTML = """
<html><body>
<div class="p-property--building">
  <h2 class="p-property__title--building">テスト一戸建て SY-001</h2>
  <dl class="p-property__information-hint">
    <dd>大阪市北区テスト町1-2-3</dd>
    <dd>地下鉄御堂筋線 「梅田」駅 徒歩5分</dd>
    <dd>賃貸一戸建て 2階建</dd>
  </dl>
  <div class="p-property__room--detail js-bukken">
    <div class="p-property__room--detailbox" data-bukken-no="999900001">
      <div class="p-property__floor">３ＬＤＫ</div>
      <p class="p-property__information-price">
        <b class="p-property__information-rent">12.5</b>万円</p>
      <li class="p-property__room-keymoney"><p>5万円</p><span>なし</span></li>
      <li class="p-property__room-floorplan"><span>75.20m²</span></li>
    </div>
  </div>
</div>
</body></html>
"""


def test_detached_house_without_room_number_still_parses(caplog: pytest.LogCaptureFixture) -> None:
    """A unit with no room-number cell parses with a warning, not an abort (FR-8)."""
    with caplog.at_level("WARNING", logger="athome_harness.scraping.list_parser"):
        summaries = parse_list_page(SYNTHETIC_DETACHED_HTML)
    assert len(summaries) == 1
    unit = summaries[0]
    assert unit.floors is None
    # The hint has no construction date, so the age stays None (never invented).
    assert unit.age is None
    assert unit.athome_key == "999900001"
    assert unit.price.rent == 125_000
    assert unit.price.deposit == 50_000
    assert unit.price.key_money == 0
    assert unit.floor_plan == "３ＬＤＫ"
    assert unit.area_m2 == pytest.approx(75.20)
    assert any("no room number" in record.message for record in caplog.records)


def test_detached_house_missing_optional_cells_are_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Absent facility list yields empty tags without failing the page."""
    with caplog.at_level("WARNING", logger="athome_harness.scraping.list_parser"):
        summaries = parse_list_page(SYNTHETIC_DETACHED_HTML)
    assert len(summaries) == 1
    assert summaries[0].usp_tags == []
    assert summaries[0].probable_negatives == []


# Synthetic month-based deposit/key-money case: a unit whose deposit is a month
# term (``2ヶ月``) and key money is ``なし``. Not from a live capture; hand-built to
# prove the raw-term preservation path for both deposit and key money (T17).
SYNTHETIC_MONTH_HTML = """
<html><body>
<div class="p-property--building">
  <h2 class="p-property__title--building">テスト月額 SY-002</h2>
  <dl class="p-property__information-hint">
    <dd>大阪市北区テスト町2-3-4</dd>
    <dd>地下鉄御堂筋線 「梅田」駅 徒歩5分</dd>
    <dd>賃貸マンション 5階建 2024年3月</dd>
  </dl>
  <div class="p-property__room--detail js-bukken">
    <div class="p-property__room--detailbox" data-bukken-no="999900002">
      <div class="p-property__floor">１Ｋ</div>
      <p class="p-property__information-price">
        <b class="p-property__information-rent">8.5</b>万円</p>
      <li class="p-property__room-keymoney"><p>2ヶ月</p><span>なし</span></li>
      <li class="p-property__room-floorplan"><span>20.00m²</span></li>
    </div>
  </div>
</div>
</body></html>
"""


def test_synthetic_month_based_deposit_preserves_raw_term(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A month-based deposit stays 0 yen but keeps its raw ``2ヶ月`` term."""
    with caplog.at_level("WARNING", logger="athome_harness.scraping.list_parser"):
        summaries = parse_list_page(SYNTHETIC_MONTH_HTML)
    assert len(summaries) == 1
    unit = summaries[0]
    assert unit.price.deposit == 0  # month term not convertible to yen here
    assert unit.price.deposit_raw == "2ヶ月"
    assert unit.price.key_money == 0
    assert unit.price.key_money_raw == "なし"
    # The month term and なし are not interchangeable.
    assert unit.price.deposit_raw != unit.price.key_money_raw
