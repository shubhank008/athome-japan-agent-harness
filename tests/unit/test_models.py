"""Unit tests for the core data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from athome_harness.config import Budgets
from athome_harness.models import (
    FilterMap,
    FilterOption,
    ListingDetail,
    ListingSummary,
    PriceBreakdown,
    Recommendation,
    RunReport,
    SearchPlan,
)


def _price(rent: int = 80000) -> PriceBreakdown:
    """Build a default price breakdown for concise test fixtures."""
    return PriceBreakdown(rent=rent, management_fee=5000, deposit=200000, key_money=0)


def _listing() -> ListingSummary:
    """Build a valid sample listing summary."""
    return ListingSummary(
        internal_id="in-0001",
        athome_key="1234567",
        url="https://www.athome.co.jp/chintai/osaka/1234567/",
        title="Sunny 1LDK near Osaka station",
        address="Osaka-shi Kia-ku",
        area_m2=40.5,
        price=_price(),
    )


@pytest.mark.parametrize(
    "costs",
    [
        (80000, 5000, 200000, 0, 285000),
        (0, 0, 0, 0, 0),
        (65000, 10000, 0, 120000, 195000),
    ],
)
def test_price_total_is_sum_of_components(costs: tuple[int, int, int, int, int]) -> None:
    """The gross cost must equal rent plus fee plus deposit plus key money."""
    rent, fee, deposit, key_money, expected = costs
    price = PriceBreakdown(rent=rent, management_fee=fee, deposit=deposit, key_money=key_money)
    assert price.total == expected


def test_price_components_reject_negative() -> None:
    """A negative rent is nonsense and must fail validation."""
    with pytest.raises(ValidationError):
        PriceBreakdown(rent=-1)


def test_price_raw_terms_preserve_month_based_terms() -> None:
    """Raw terms distinguish a ``1ヶ月`` month term from ``なし``/zero.

    Both share numeric 0, but the raw field keeps them apart.
    """
    month = PriceBreakdown(rent=80000, deposit=0, deposit_raw="1ヶ月")
    none_ = PriceBreakdown(rent=80000, deposit=0, deposit_raw="なし")
    assert month.deposit == none_.deposit == 0
    assert month.deposit_raw == "1ヶ月"
    assert none_.deposit_raw == "なし"
    assert month.deposit_raw != none_.deposit_raw


def test_price_raw_terms_default_when_omitted() -> None:
    """Existing callers omitting raw fields keep working (backwards compatible)."""
    price = PriceBreakdown(rent=80000, deposit=200000, key_money=0)
    assert price.deposit == 200000
    assert price.key_money == 0
    assert price.deposit_raw is None
    assert price.key_money_raw is None
    assert price.total == 280000


def test_area_must_be_non_negative() -> None:
    """Floor area cannot be negative."""
    negative = _listing().model_dump()
    negative["area_m2"] = -1.0
    with pytest.raises(ValidationError):
        ListingSummary.model_validate(negative)
    zero = _listing().model_dump()
    zero["area_m2"] = 0.0
    assert ListingSummary.model_validate(zero).area_m2 == 0.0


def test_recommendation_rank_must_be_positive() -> None:
    """Rank is 1-based and must be strictly positive."""
    with pytest.raises(ValidationError):
        Recommendation(listing_id="in-0001", rank=0)
    with pytest.raises(ValidationError):
        Recommendation(listing_id="in-0001", rank=-3)
    assert Recommendation(listing_id="in-0001", rank=1).rank == 1


def test_listing_summary_defaults_optional_fields() -> None:
    """Optional fields fall back to None / empty lists."""
    listing = _listing()
    assert listing.station is None
    assert listing.usp_tags == []
    assert listing.probable_negatives == []
    assert listing.photo_urls == []


def test_listing_detail_extends_summary() -> None:
    """Detail keeps every summary field plus the full-page extras."""
    detail = ListingDetail(**_listing().model_dump(), description="Large balcony.")
    assert detail.area_m2 == 40.5
    assert detail.description == "Large balcony."
    assert detail.floor_plan_image_url is None
    assert detail.facility_features == []


def test_search_plan_round_trip() -> None:
    """A plan carries flow, hard filters, soft prefs, and optional budgets."""
    plan = SearchPlan(
        flow="rent",
        prefecture="osaka",
        cities=["osaka-shi"],
        hard_filters={"MADORI": ["km01", "km02"]},
        soft_prefs=["quiet", "near station"],
        budgets=Budgets(),
    )
    assert plan.flow == "rent"
    assert plan.hard_filters["MADORI"] == ["km01", "km02"]
    assert plan.budgets is not None


def test_filter_map_validates_options() -> None:
    """Filter map nests flow -> filter -> options and keeps the version/hash."""
    fm = FilterMap(
        version=1,
        content_hash="abc123",
        mappings={
            "rent": {
                "MADORI": [
                    FilterOption(code="km01", label="1K"),
                    FilterOption(code="km02", label="1LDK"),
                ]
            }
        },
    )
    assert fm.mappings["rent"]["MADORI"][1].label == "1LDK"
    assert fm.version == 1 and fm.content_hash == "abc123"


def test_run_report_holds_session_results() -> None:
    """RunReport gathers query, plan, counts, shortlist, and recommendations."""
    listing = _listing()
    report = RunReport(
        query="1LDK under 120k",
        plan=SearchPlan(flow="rent", prefecture="osaka"),
        results_seen=5,
        pages_scraped=1,
        shortlist=[listing],
        recommendations=[
            Recommendation(
                listing_id="in-0001",
                rank=1,
                reasons=["Meets budget"],
                satisfied_constraints=["rent"],
            )
        ],
        budgets_consumed=Budgets(),
        partial=False,
    )
    assert report.results_seen == 5
    assert report.shortlist[0].internal_id == "in-0001"
    assert report.recommendations[0].rank == 1
    assert report.partial is False


def test_run_report_counts_reject_negative() -> None:
    """Result counts cannot be negative."""
    with pytest.raises(ValidationError):
        RunReport(query="q", plan=SearchPlan(flow="rent", prefecture="osaka"), results_seen=-1)
