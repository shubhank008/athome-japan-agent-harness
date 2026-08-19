"""Unit tests for the AtHome detail-page parser (M3 T16).

Primary cases parse the real captured public detail page under
``tests/fixtures/`` (genuine live data). A synthetic minimal DOM labelled as such
covers the missing-optional-field detail path (no floor-plan image, no facility
category rows, no map-link suffix) purely to prove graceful handling.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from athome_harness.scraping.detail_parser import parse_detail_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DETAIL_FIXTURE = FIXTURES / "detail_1101570928.html"
DETAIL_FIXTURE_1131157822 = FIXTURES / "detail_1131157822.html"
DETAIL_FIXTURE_1122949022 = FIXTURES / "detail_1122949022.html"

# Fixed frame for deterministic age assertions; matches the build dates captured.
AGE_REF_DATE = date(2026, 8, 18)


def _load_detail() -> str:
    return DETAIL_FIXTURE.read_text(encoding="utf-8")


def _load_detail_1131157822() -> str:
    return DETAIL_FIXTURE_1131157822.read_text(encoding="utf-8")


def _load_detail_1122949022() -> str:
    return DETAIL_FIXTURE_1122949022.read_text(encoding="utf-8")


def test_captured_detail_identity() -> None:
    """Key, canonical URL, title, and address come off the captured page."""
    detail = parse_detail_page(_load_detail())
    assert detail.athome_key == "1101570928"
    assert detail.internal_id == "1101570928"
    assert detail.url == "https://www.athome.co.jp/chintai/1101570928/"
    assert "Ｆ＋ｓｔｙｌｅ東大阪本庄１号館" in detail.title
    assert detail.address == "大阪府東大阪市本庄２丁目"


def test_captured_detail_price() -> None:
    """Rent, management fee, deposit, and key money parse from the price block."""
    detail = parse_detail_page(_load_detail())
    assert detail.price.rent == 55_800
    assert detail.price.management_fee == 5_000
    assert detail.price.deposit == 0  # 敷金: なし
    assert detail.price.key_money == 70_000


def test_captured_detail_property_fields() -> None:
    """Floor plan, area, building type, floors, station, and walk are parsed."""
    detail = parse_detail_page(_load_detail())
    assert detail.floor_plan == "１Ｋ"
    assert detail.area_m2 == pytest.approx(24.99)
    assert detail.building_type == "賃貸アパート"
    assert detail.floors == "3階建 / 1階"
    assert detail.station == "荒本"
    assert detail.walk_minutes == 10.0


def test_captured_detail_photos_and_floor_plan() -> None:
    """The full photo set and the dedicated floor-plan image URL are extracted."""
    detail = parse_detail_page(_load_detail())
    assert len(detail.photo_urls) == 27
    assert detail.floor_plan_image_url is not None
    assert detail.floor_plan_image_url.startswith("https://www.athome.co.jp")
    assert detail.floor_plan_image_url in detail.photo_urls


def test_captured_detail_usp_and_facilities() -> None:
    """USP tags, facility features, and empty probable negatives are extracted."""
    detail = parse_detail_page(_load_detail())
    assert "バス・トイレ別" in detail.usp_tags
    assert any("システムキッチン" in f for f in detail.facility_features)
    # This captured page has no disabled-facility markers.
    assert detail.probable_negatives == []


def test_captured_detail_has_description() -> None:
    """The free-text remarks (備考) form the description."""
    detail = parse_detail_page(_load_detail())
    assert "リモートワーク" in detail.description


# Synthetic edge case: a detail page whose optional cells are missing (no floor
# plan image, no facility category rows, no map-link suffix). Hand-built only to
# exercise graceful handling; not derived from a live capture.
SYNTHETIC_DETAIL_HTML = """
<html><body>
<title>テストマンション ２０１ １ＤＫ【アットホーム】[555500123]</title>
<div class="paymentInfo typeChintai">
  <dl class="data"><dt>賃料：</dt><dd>9万円</dd></dl>
  <dl class="data"><dt>管理費等</dt><dd>8,000円</dd></dl>
</div>
<table class="dataTbl">
  <tr><th>建物名・部屋番号</th><td>テストマンション ２０１</td></tr>
  <tr><th>間取り</th><td>１ＤＫ</td></tr>
  <tr><th>物件種目</th><td>賃貸マンション</td></tr>
  <tr><th>専有面積</th><td>40.10m²</td></tr>
  <tr><th>階建 / 階</th><td>5階建 / 2階</td></tr>
  <tr><th>住所</th><td>大阪市北区テスト４丁目</td></tr>
  <tr><th>交通</th><td>阪急千里線 / テスト駅 徒歩3分</td></tr>
</table>
</body></html>
"""


def test_synthetic_detail_missing_optionals_parse_gracefully(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A detail page with no photos, floor plan image, or facility rows still parses."""
    with caplog.at_level("WARNING", logger="athome_harness.scraping.detail_parser"):
        detail = parse_detail_page(SYNTHETIC_DETAIL_HTML)
    assert detail.athome_key == "555500123"
    assert detail.photo_urls == []
    assert detail.floor_plan_image_url is None
    assert detail.facility_features == []
    assert detail.probable_negatives == []
    assert detail.usp_tags == []
    assert detail.floor_plan == "１ＤＫ"
    assert detail.area_m2 == pytest.approx(40.10)
    assert detail.station == "テスト"
    assert detail.walk_minutes == 3.0


def test_captured_detail_age_from_build_date() -> None:
    """The new-build detail page exposes a near-zero age, never a silent None.

    The ``築年月`` value (2026年7月) is an observed construction date, so it must be
    converted to a small fractional age rather than recorded as ``None``.
    """
    detail = parse_detail_page(_load_detail(), ref_date=AGE_REF_DATE)
    assert detail.age is not None
    assert 0.0 <= detail.age < 1.0


def test_captured_detail_age_from_older_build() -> None:
    """An older captured build (2026年2月) yields a larger, still-positive age."""
    detail = parse_detail_page(
        _load_detail_1131157822(), ref_date=AGE_REF_DATE
    )
    assert detail.age is not None
    assert 0.4 <= detail.age < 1.0


def test_captured_detail_1122949022_identity_and_price() -> None:
    """Third fixture parses correctly: key, URL, title, address, and price."""
    detail = parse_detail_page(_load_detail_1122949022())
    assert detail.athome_key == "1122949022"
    assert detail.internal_id == "1122949022"
    assert detail.url == "https://www.athome.co.jp/chintai/1122949022/"
    assert "みおつくし大池橋" in detail.title
    assert detail.address == "大阪府大阪市生野区中川西３丁目"
    assert detail.price.rent == 59_500
    assert detail.price.management_fee == 5_000
    assert detail.price.deposit == 0
    assert detail.price.deposit_raw == "なし"
    assert detail.price.key_money == 0
    assert detail.price.key_money_raw == "なし"


def test_captured_detail_1122949022_property_fields() -> None:
    """Third fixture property fields: floor plan, area, type, floors, station."""
    detail = parse_detail_page(_load_detail_1122949022())
    assert detail.floor_plan == "１Ｋ"
    assert detail.area_m2 == pytest.approx(22.62)
    assert detail.building_type == "賃貸マンション"
    assert detail.floors == "10階建 / 9階"
    assert detail.station == "桃谷"
    assert detail.walk_minutes == 15.0


def test_captured_detail_1122949022_age() -> None:
    """Third fixture age from 築年月 2026年2月 against fixed ref date."""
    detail = parse_detail_page(_load_detail_1122949022(), ref_date=AGE_REF_DATE)
    assert detail.age is not None
    assert 0.4 <= detail.age < 1.0


def test_captured_detail_1122949022_facilities() -> None:
    """Third fixture has facility features and no disabled markers."""
    detail = parse_detail_page(_load_detail_1122949022())
    assert len(detail.facility_features) > 0
    assert detail.probable_negatives == []


def test_captured_detail_price_raw_terms() -> None:
    """Deposit/key money keep their raw terms alongside yen values."""
    detail = parse_detail_page(_load_detail())
    assert detail.price.deposit == 0
    assert detail.price.deposit_raw == "なし"
    assert detail.price.key_money == 70_000
    assert detail.price.key_money_raw == "7万円"


def test_synthetic_detail_age_years_form() -> None:
    """A plain ``築年数`` value ``7年`` is used directly as the age in years."""
    html = (
        "<html><body><title>[999900100]</title>"
        '<table class="dataTbl">'
        "<tr><th>築年数</th><td>7年</td></tr>"
        "</table></body></html>"
    )
    detail = parse_detail_page(html, ref_date=AGE_REF_DATE)
    assert detail.age == pytest.approx(7.0)


# Synthetic disabled-facility detail case: facility rows where some items carry
# the ``facility_disabled-list`` class. Hand-built (not a live capture) because
# the captured detail pages have no disabled markers; the list fixture covers the
# list path, this covers the detail path (T16).
SYNTHETIC_DISABLED_DETAIL_HTML = """
<html><body>
<title>テスト物件 ２０１ １Ｋ【アットホーム】[999900101]</title>
<div class="paymentInfo typeChintai">
  <dl class="data"><dt>賃料：</dt><dd>6万円</dd></dl>
</div>
<table class="dataTbl">
  <tr><th>バス・トイレ</th>
    <td>
      <p>バス・トイレ別</p>
      <p class="facility_disabled-list">浴室乾燥機</p>
    </td>
  </tr>
  <tr><th>キッチン</th>
    <td><p>システムキッチン、ガスコンロ</p></td>
  </tr>
  <tr><th>セキュリティー</th>
    <td><p class="facility_disabled-list">オートロック</p></td>
  </tr>
</table>
</body></html>
"""


def test_synthetic_detail_disabled_facilities_are_probable_negatives() -> None:
    """Disabled-facility markers populate probable_negatives, enabled ones USP."""
    detail = parse_detail_page(SYNTHETIC_DISABLED_DETAIL_HTML)
    assert "バス・トイレ別" in detail.facility_features
    assert "システムキッチン" in detail.facility_features
    assert "浴室乾燥機" in detail.probable_negatives
    assert "オートロック" in detail.probable_negatives
    # Enabled items never leak into the negatives, and vice versa.
    assert not any("システムキッチン" in n for n in detail.probable_negatives)
    assert not any("オートロック" in f for f in detail.facility_features)
