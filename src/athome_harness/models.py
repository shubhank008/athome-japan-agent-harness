"""Core pydantic v2 data models for the AtHome harness.

These models are the typed contract between the parsing, LLM, storage, and reporting
layers. Field shapes follow SPEC.md section 3 (Data models) exactly. Price, area, and
rank invariants enforced here are unit-tested in ``tests/unit/test_models.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from athome_harness.config import Budgets


class PriceBreakdown(BaseModel):
    """Monetary breakdown of a listing as shown in reports (FR-9).

    Rent recurs monthly; management fee is monthly; deposit and key money are paid
    upfront. The report presents all four together, so ``total`` is their sum.
    """

    rent: int = Field(ge=0, description="Monthly rent in yen.")
    management_fee: int = Field(default=0, ge=0, description="Monthly management fee in yen.")
    deposit: int = Field(default=0, ge=0, description="Upfront deposit in yen.")
    key_money: int = Field(default=0, ge=0, description="Upfront key money in yen.")

    @property
    def total(self) -> int:
        """Sum of all four components, used as the gross cost summary."""
        return self.rent + self.management_fee + self.deposit + self.key_money


class ListingSummary(BaseModel):
    """One rentable or purchasable unit as parsed from an AtHome results page.

    Multi-unit buildings yield one summary per unit, each sharing a building identity.
    Every field is the smallest unit the LLM funnel and the report need (SPEC section 3).
    """

    internal_id: str = Field(description="Stable internal property ID used for dedupe.")
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
        default=None, ge=0, description="Building age in years; None for new builds."
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
    """A fully scraped listing detail page (SPEC section 3).

    Extends :class:`ListingSummary` with the full text fields, the complete photo set,
    and the floor-plan image URL.
    """

    description: str = Field(
        default="", description="Full free-text description from the detail page."
    )
    floor_plan_image_url: str | None = Field(
        default=None, description="URL of the floor-plan image, when present."
    )
    facility_features: list[str] = Field(
        default_factory=list, description="Additional facility features listed in detail."
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
