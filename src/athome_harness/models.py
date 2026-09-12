"""Core pydantic v2 data models for the AtHome harness.

These models are the typed contract between the parsing, LLM, storage, and reporting
layers. Field shapes follow SPEC.md section 3 (Data models) exactly. Price, area, and
rank invariants enforced here are unit-tested in ``tests/unit/test_models.py``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from athome_harness.config import Budgets


class PriceBreakdown(BaseModel):
    """Monetary breakdown of a listing as shown in reports (FR-9).

    Rent recurs monthly; management fee is monthly; deposit and key money are paid
    upfront. The report presents all four together, so ``total`` is their sum.
    """

    rent: int = Field(ge=0, description="Monthly rent in yen.")
    management_fee: int = Field(default=0, ge=0, description="Monthly management fee in yen.")
    deposit: int | None = Field(
        default=None, ge=0, description="Upfront deposit in yen when numeric."
    )
    key_money: int | None = Field(
        default=None, ge=0, description="Upfront key money in yen when numeric."
    )
    # Raw deposit/key-money terms as displayed (e.g. ``1ヶ月``), preserved so a
    # month-based term is never indistinguishable from ``なし``/zero, which
    # cannot be converted to yen without the rent context. ``None`` when absent.
    deposit_raw: str | None = Field(
        default=None, description="Upfront deposit term as displayed, e.g. 1ヶ月."
    )
    key_money_raw: str | None = Field(
        default=None, description="Upfront key-money term as displayed, e.g. 1ヶ月."
    )

    @property
    def total(self) -> int:
        """Sum known yen components, excluding non-numeric raw terms."""
        return self.rent + self.management_fee + (self.deposit or 0) + (self.key_money or 0)


class ListingCompleteness(StrEnum):
    """Observed completeness level for an AtHome listing record."""

    SUMMARY_PARTIAL = "summary_partial"
    SUMMARY_COMPLETE = "summary_complete"
    DETAIL_COMPLETE = "detail_complete"


class Agency(BaseModel):
    """AtHome agency record keyed by the ``kaiinNo`` member number."""

    kaiin_no: str = Field(description="AtHome agency member number (kaiinNo).")
    kaiin_link_no: str | None = Field(default=None, description="AtHome agency link number.")
    name: str | None = Field(default=None, description="Agency display name.")
    postal_code: str | None = Field(default=None, description="Agency postal code.")
    address: str | None = Field(default=None, description="Agency address.")
    phone: str | None = Field(default=None, description="Agency telephone or fax text.")
    url: str | None = Field(default=None, description="Agency detail URL, when supplied.")
    representative: str | None = Field(default=None, description="Agency representative name.")
    domain: str | None = Field(default=None, description="Agency web domain.")
    access: str | None = Field(default=None, description="Agency station access text.")
    business_hours: str | None = Field(default=None, description="Agency operating hours.")
    holidays: str | None = Field(default=None, description="Agency regular holidays.")
    features: str | None = Field(default=None, description="Raw agency feature text.")
    associations: str | None = Field(default=None, description="Raw association membership text.")
    license_number: str | None = Field(default=None, description="Agency license text.")
    raw: dict[str, object] = Field(
        default_factory=dict, description="Unmapped raw kaiinInfo values."
    )


class ImageRecord(BaseModel):
    """One structured detail image, retaining source metadata."""

    url: str = Field(description="Source image URL or path.")
    title: str | None = None
    category: str | None = None
    raw: dict[str, object] = Field(default_factory=dict)


class AccessRecord(BaseModel):
    """One structured transit/access option."""

    line_name: str | None = None
    station_name: str | None = None
    walk_time: str | None = None
    raw: dict[str, object] = Field(default_factory=dict)


class FacilityRecord(BaseModel):
    """One nearby facility with distance and source metadata."""

    title: str
    category: str | None = None
    distance: str | None = None
    image_url: str | None = None
    raw: dict[str, object] = Field(default_factory=dict)


class FeatureGroup(BaseModel):
    """One categorized facility feature group."""

    title: str
    text: str
    raw: dict[str, object] = Field(default_factory=dict)


class StructuredDetail(BaseModel):
    """Rich server-state data retained separately from the LLM projection."""

    raw: dict[str, object] = Field(default_factory=dict)
    romanized: dict[str, str] = Field(default_factory=dict)
    access: list[AccessRecord] = Field(default_factory=list)
    images: list[ImageRecord] = Field(default_factory=list)
    nearby_facilities: list[FacilityRecord] = Field(default_factory=list)
    feature_groups: list[FeatureGroup] = Field(default_factory=list)
    surrounding_info: dict[str, object] = Field(default_factory=dict)
    cost_info: dict[str, object] = Field(default_factory=dict)
    appeal_point: str | None = None
    building_info: dict[str, object] = Field(default_factory=dict)
    other_property_info: dict[str, object] = Field(default_factory=dict)


class ListingSummary(BaseModel):
    """One rentable or purchasable unit as parsed from an AtHome results page.

    Multi-unit buildings yield one summary per unit, each sharing a building identity.
    Every field is the smallest unit the LLM funnel and the report need (SPEC section 3).
    """

    internal_id: str = Field(description="Stable internal property ID used for dedupe.")
    completeness: ListingCompleteness = Field(
        default=ListingCompleteness.SUMMARY_PARTIAL,
        description="Highest observed listing data completeness level.",
    )
    detail_fetched_at: datetime | None = Field(
        default=None, description="UTC timestamp when detail data was fetched."
    )
    detail_fresh_until: datetime | None = Field(
        default=None, description="UTC timestamp through which fetched detail is fresh."
    )
    agency: Agency | None = Field(default=None, description="Persisted listing agency, when known.")

    @model_validator(mode="after")
    def validate_detail_freshness(self) -> ListingSummary:
        """Require a complete timestamp pair when detail freshness is recorded."""
        fetched_at = self.detail_fetched_at
        fresh_until = self.detail_fresh_until
        if (fetched_at is None) != (fresh_until is None):
            raise ValueError("detail_fetched_at and detail_fresh_until must be provided together")
        if fetched_at is not None and fresh_until is not None and fresh_until < fetched_at:
            raise ValueError("detail_fresh_until must not precede detail_fetched_at")
        return self

    athome_key: str = Field(description="AtHome BKLISTID listing key.")
    url: str = Field(description="Canonical AtHome listing URL.")
    title: str = Field(description="Human-readable listing title.")
    address: str = Field(description="Street/presented address of the unit.")
    station: str | None = Field(default=None, description="Nearest station name, when known.")
    walk_minutes: float | None = Field(
        default=None, ge=0, description="Minutes walking from the station."
    )
    building_type: str | None = Field(
        default=None, description="Building category (e.g. apartment, house)."
    )
    floors: str | None = Field(default=None, description="Floor/build-height descriptor, raw text.")
    age: float | None = Field(
        default=None,
        ge=0,
        description="Building age in years, rounded to one decimal place.",
    )
    age_raw: str | None = Field(default=None, description="Raw displayed age term.")
    construction_date: str | None = Field(
        default=None, description="Raw construction date when exposed."
    )
    age_display: str | None = Field(
        default=None, description="Human-readable age such as '1 month old'."
    )
    price: PriceBreakdown = Field(description="Monetary breakdown for the unit.")
    floor_plan: str | None = Field(default=None, description="Layout descriptor (e.g. 1LDK).")
    area_m2: float = Field(ge=0, description="Floor area in square meters.")
    usp_tags: list[str] = Field(default_factory=list, description="Confirmed feature highlights.")
    probable_negatives: list[str] = Field(
        default_factory=list,
        description="Features plausibly absent, from disabled-feature DOM markers.",
    )
    photo_urls: list[str] = Field(
        default_factory=list, description="Thumbnail photo URLs from the list page."
    )


class ListingDetail(ListingSummary):
    """A listing summary hydrated with detail-page data and status metadata."""

    completeness: ListingCompleteness = Field(
        default=ListingCompleteness.DETAIL_COMPLETE,
        description="Detail records are complete unless explicitly marked partial.",
    )
    listing_detail: bool = Field(
        default=False,
        description="True when the detail page supplied usable listing data.",
    )
    detail_failure_reason: str | None = Field(
        default=None,
        description="Operator-safe reason detail hydration was incomplete or failed.",
    )

    @model_validator(mode="after")
    def infer_legacy_detail_completeness(self) -> ListingDetail:
        """Map legacy successful detail payloads to the explicit detail state."""
        if (
            self.completeness is ListingCompleteness.SUMMARY_PARTIAL
            and not self.listing_detail
            and self.detail_failure_reason is None
        ):
            self.completeness = ListingCompleteness.DETAIL_COMPLETE
        return self

    building_name: str | None = Field(default=None, description="Canonical building name.")
    building_structure: str | None = Field(default=None, description="Building structure.")
    total_units: str | None = Field(default=None, description="Displayed total unit count.")
    contract_period: str | None = Field(default=None, description="Displayed contract period.")
    pickup_features: list[str] = Field(
        default_factory=list, description="Confirmed PICK UP feature labels."
    )
    remarks: str | None = Field(default=None, description="Raw detail remarks.")
    description: str = Field(
        default="", description="Full free-text description from the detail page."
    )
    floor_plan_image_url: str | None = Field(
        default=None, description="URL of the floor-plan image, when present."
    )
    facility_features: list[str] = Field(
        default_factory=list, description="Additional facility features listed in detail."
    )
    structured_detail: StructuredDetail | None = Field(
        default=None, description="Rich server-state detail retained for persistence."
    )


class Recommendation(BaseModel):
    """A single shortlisted listing ranked by the recommender (US-004).

    ``listing_id`` references the source summary; rank is 1-based and strictly
    positive. Constraint arrays cite which query filters each recommendation does or
    does not satisfy.
    """

    listing_id: str = Field(description="Internal ID of the recommended listing.")
    rank: int = Field(gt=0, description="1-based rank; must be positive.")
    reasons: list[str] = Field(
        default_factory=list, description="Human-readable rationale for the pick."
    )
    satisfied_constraints: list[str] = Field(
        default_factory=list, description="Query constraints this listing satisfies."
    )
    violated_constraints: list[str] = Field(
        default_factory=list, description="Query constraints this listing violates."
    )
    probable_negatives: list[str] = Field(
        default_factory=list, description="Caveats from disabled-feature markers."
    )
    listing: ListingSummary | None = Field(
        default=None, description="Embedded source data, when rendered into a report."
    )


class SearchPlan(BaseModel):
    """The interpreted search intent produced from a natural-language query (US-001).

    ``hard_filters`` maps AtHome filter names to the list of selected codes (typed by
    the cardinality contract in SPEC.md section 1.1); unmappable constraint details ride
    as ``soft_prefs`` instead of being silently dropped.
    """

    flow: Literal["rent", "buy"] = Field(description="Rental or purchase search flow.")
    prefecture: str = Field(description="Target prefecture, e.g. osaka.")
    cities: list[str] = Field(
        default_factory=list, description="Target cities within the prefecture."
    )
    hard_filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Filter name to selected codes for hard, encoder-enforced filters.",
    )
    soft_prefs: list[str] = Field(
        default_factory=list,
        description="Natural-language preferences used only for LLM soft scoring.",
    )
    budgets: Budgets | None = Field(
        default=None, description="Budget overrides for this search, if any."
    )


class FilterOption(BaseModel):
    """One selectable option inside a filter (SPEC.md section 2)."""

    code: str = Field(description="AtHome filter code, e.g. kc123.")
    label: str = Field(description="Human-readable option label.")


class FilterMap(BaseModel):
    """Versioned filter mapping, keyed by flow then filter field (SPEC.md section 2).

    ``mappings[flow][filter name]`` yields the ordered list of :class:`FilterOption`.
    Codes are context-dependent, so the map is always looked up by (flow, filter name).
    ``content_hash`` fingerprints the source snapshot for the weekly refresh tool.
    """

    version: int = Field(description="Schema version the harness understands.")
    content_hash: str = Field(
        description="SHA-256 (truncated) of the source snapshot this map was built from."
    )
    mappings: dict[str, dict[str, list[FilterOption]]] = Field(
        description="Flow -> filter name -> ordered option list."
    )


class RunReport(BaseModel):
    """End-of-session summary produced after a search run (US-004, FR-7).

    Carries the original query and plan, harvest and recommendation results, and whether
    a budget or block aborted the run early (partial).
    """

    query: str = Field(description="Original natural-language query.")
    plan: SearchPlan = Field(description="The search plan actually executed.")
    results_seen: int = Field(
        default=0, ge=0, description="Total listings harvested across all pages."
    )
    pages_scraped: int = Field(default=0, ge=0, description="Number of result pages fetched.")
    shortlist: list[ListingSummary] = Field(
        default_factory=list, description="Top-X shortlist from the LLM scorer."
    )
    recommendations: list[Recommendation] = Field(
        default_factory=list, description="Top-Y recommendations with reasons."
    )
    budgets_consumed: Budgets | None = Field(
        default=None, description="Budgets applied during the run."
    )
    partial: bool = Field(
        default=False,
        description="True when a budget or block aborted the run before completion.",
    )
