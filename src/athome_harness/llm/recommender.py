"""LLM recommender and report rendering (milestone M4, T21).

Ranks fully scraped :class:`ListingDetail` values into the top Y
:class:`Recommendation` values with human reasons, then renders the result as
markdown and as structured JSON (US-004). Every recommendation cites which
query constraints it satisfies and which it violates, and surfaces the
listing's Probable Negatives (disabled-feature markers) as caveats, distinct
from confirmed features.

The ranking itself is delegated to an injected :class:`BaseLLMProvider`; the
module otherwise performs no network I/O. Report rendering is pure functions so
the golden-file tests can assert exact output without a provider.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from athome_harness.llm.base import BaseLLMProvider
from athome_harness.models import ListingDetail, Recommendation, SearchPlan

logger = logging.getLogger(__name__)

DEFAULT_TOP_Y = 5

_SYSTEM_PROMPT = (
    "You are a Japanese home-finding assistant. Rank the provided fully "
    "scraped listings against the user's soft preferences and hard filters. "
    "Pick the top listings that best fit, each with concrete reasons tied to "
    "the query constraints. Cite, per listing, which soft/hard constraints it "
    "satisfies and which (if any) it violates. Return JSON with a 'ranked' "
    "array of entries, each with listing_id, reasons (list), "
    "satisfied_constraints (list), and violated_constraints (list)."
)


class RecommendationEntry(BaseModel):
    """Model output for one ranked recommendation before enrichment."""

    listing_id: str = Field(description="Internal ID of the recommended listing.")
    reasons: list[str] = Field(default_factory=list, description="Concrete reasons.")
    satisfied_constraints: list[str] = Field(
        default_factory=list, description="Constraints this listing satisfies."
    )
    violated_constraints: list[str] = Field(
        default_factory=list, description="Constraints this listing violates."
    )


class RecommendationOutput(BaseModel):
    """Top-Y model output: an ordered list of ranked entries."""

    ranked: list[RecommendationEntry] = Field(
        default_factory=list, description="Ranked recommendations, best first."
    )


class Recommender:
    """Ranks scraped details into top-Y :class:`Recommendation` values."""

    def __init__(self, provider: BaseLLMProvider) -> None:
        self._provider = provider

    def recommend(
        self,
        details: list[ListingDetail],
        plan: SearchPlan,
        *,
        top_y: int | None = None,
        temperature: float = 0.0,
    ) -> list[Recommendation]:
        """Return ``details`` ranked into the configured top-``top_y``.

        The plan's soft preferences and hard-filter names are presented as the
        constraints the model scores against. Unknown or duplicate model IDs
        are ignored while output ranks remain contiguous.
        """
        if not details:
            return []
        limit = DEFAULT_TOP_Y if top_y is None else max(0, top_y)
        if limit == 0:
            return []
        constraints = self._describe_constraints(plan)
        by_id = {det.internal_id: det for det in details}
        user_lines = "\n".join(self._serialize(det) for det in details)
        user = (
            f"Constraints:\n{constraints}\n\nListings:\n{user_lines}\n\n"
            f"Return the top {limit} listings as JSON ranked best first."
        )
        output, usage = self._provider.complete_json(
            system=_SYSTEM_PROMPT,
            user=user,
            schema=RecommendationOutput,
            temperature=temperature,
        )
        logger.debug(
            "[RECOMMEND_TOKENS] prompt=%d completion=%d ranked=%d",
            usage.prompt_tokens,
            usage.completion_tokens,
            len(output.ranked),
        )
        recommendations: list[Recommendation] = []
        seen_ids: set[str] = set()
        for entry in output.ranked:
            if len(recommendations) >= limit:
                break
            detail = by_id.get(entry.listing_id)
            if detail is None:
                logger.warning("recommender referenced unknown listing %s", entry.listing_id)
                continue
            if entry.listing_id in seen_ids:
                continue
            seen_ids.add(entry.listing_id)
            recommendations.append(
                Recommendation(
                    listing_id=entry.listing_id,
                    rank=len(recommendations) + 1,
                    reasons=entry.reasons,
                    satisfied_constraints=entry.satisfied_constraints,
                    violated_constraints=entry.violated_constraints,
                    probable_negatives=list(detail.probable_negatives),
                    listing=detail,
                )
            )
        return recommendations

    @staticmethod
    def _describe_constraints(plan: SearchPlan) -> str:
        """Flatten the plan's hard filters and soft prefs into constraint text."""
        hard = ", ".join(f"{name}={values}" for name, values in plan.hard_filters.items())
        soft = "; ".join(plan.soft_prefs)
        pieces = [f"flow={plan.flow}", f"prefecture={plan.prefecture}"]
        if plan.cities:
            pieces.append(f"cities={','.join(plan.cities)}")
        if hard:
            pieces.append(f"hard_filters: {hard}")
        if soft:
            pieces.append(f"soft_prefs: {soft}")
        return "\n".join(pieces)

    @staticmethod
    def _serialize(detail: ListingDetail) -> str:
        """Serialize one detail to a compact JSON line for the prompt."""
        data = detail.model_dump()
        data.pop("photo_urls", None)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def render_markdown(recommendations: list[Recommendation], query: str = "") -> str:
    """Render recommendations as a human-readable markdown report.

    Includes, per recommendation, the FR-9 fields (title, price, address,
    station and walk, floor plan, area, age, USP tags, URL), the reasons, and
    the Probable Negatives as caveats.
    """
    lines: list[str] = []
    if query:
        lines.append(f"# Housing recommendations\n\nQuery: {query}\n")
    for rec in recommendations:
        listing = rec.listing
        title = listing.title if listing is not None else rec.listing_id
        lines.append(f"## {rec.rank}. {title}\n")
        if listing is not None:
            price = listing.price
            lines.append(
                f"- Rent: {price.rent:,} yen, management {price.management_fee:,} yen, "
                f"deposit {price.deposit:,} yen, key money {price.key_money:,} yen"
            )
            if listing.address:
                lines.append(f"- Address: {listing.address}")
            if listing.station:
                walk = (
                    f" ({listing.walk_minutes:g} min walk)"
                    if listing.walk_minutes is not None
                    else ""
                )
                lines.append(f"- Station: {listing.station}{walk}")
            if listing.floor_plan:
                lines.append(f"- Floor plan: {listing.floor_plan}")
            if listing.area_m2:
                lines.append(f"- Area: {listing.area_m2:g} m2")
            if listing.age is not None:
                lines.append(f"- Age: {listing.age:g} years")
            if listing.usp_tags:
                lines.append(f"- USP: {', '.join(listing.usp_tags)}")
            if listing.url:
                lines.append(f"- Link: {listing.url}")
        if rec.reasons:
            lines.append("- Reasons:")
            lines.extend(f"  - {reason}" for reason in rec.reasons)
        if rec.satisfied_constraints:
            lines.append(f"- Satisfies: {', '.join(rec.satisfied_constraints)}")
        if rec.violated_constraints:
            lines.append(f"- Violates: {', '.join(rec.violated_constraints)}")
        if rec.probable_negatives:
            lines.append(f"- Caveats: {', '.join(rec.probable_negatives)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(recommendations: list[Recommendation]) -> str:
    """Render recommendations as a structured JSON document.

    Each entry embeds the source listing data so the JSON is self-contained for
    downstream consumers. Output is deterministic (sorted keys, compact separators).
    """

    def _entry(rec: Recommendation) -> dict[str, Any]:
        return {
            "rank": rec.rank,
            "listing_id": rec.listing_id,
            "reasons": rec.reasons,
            "satisfied_constraints": rec.satisfied_constraints,
            "violated_constraints": rec.violated_constraints,
            "probable_negatives": rec.probable_negatives,
            "listing": rec.listing.model_dump(mode="json") if rec.listing is not None else None,
        }

    payload = {"recommendations": [_entry(rec) for rec in recommendations]}
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
