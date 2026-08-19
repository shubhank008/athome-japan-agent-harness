"""Unit tests for the recommender and report rendering (M4, T21).

Verifies top-Y ranking with reasons and satisfied/violated constraints,
probable-negative caveats from the detail data, and golden-file tests for the
markdown and structured JSON report renderers.
"""

from __future__ import annotations

import json
from pathlib import Path

from athome_harness.llm.recommender import Recommender, render_json, render_markdown
from athome_harness.models import ListingDetail, PriceBreakdown, SearchPlan
from tests.unit._fakes import SequenceProvider

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _detail(internal_id: str, title: str) -> ListingDetail:
    """Build a fully populated ListingDetail for ranking tests."""
    return ListingDetail(
        internal_id=internal_id,
        athome_key=internal_id,
        url=f"https://www.athome.co.jp/chintai/{internal_id}/",
        title=title,
        address="Osaka-shi, Kita-ku",
        station="Umeda",
        walk_minutes=5.0,
        price=PriceBreakdown(
            rent=100_000,
            management_fee=5_000,
            deposit=100_000,
            key_money=0,
            deposit_raw="1ヶ月",
            key_money_raw="なし",
        ),
        area_m2=30.0,
        floor_plan="1DK",
        age=3.0,
        usp_tags=["system kitchen"],
        probable_negatives=["pet consultation"],
        description="Bright corner unit.",
    )


def _plan() -> SearchPlan:
    """A rent plan with soft prefs used as scoring constraints."""
    return SearchPlan(
        flow="rent",  # type: ignore[arg-type]
        prefecture="osaka",
        hard_filters={"MADORI": ["km003"]},
        soft_prefs=["near station", "pet friendly"],
    )


def _ranked_json() -> str:
    return (
        '{"ranked": ['
        '{"listing_id": "a", "reasons": ["5 min to Umeda"], '
        '"satisfied_constraints": ["near station"], "violated_constraints": []},'
        '{"listing_id": "b", "reasons": ["quiet"], '
        '"satisfied_constraints": [], "violated_constraints": ["pet friendly"]}'
        "]}"
    )


def test_ranks_top_y_with_constraints() -> None:
    """Details rank into top-Y Recommendations with reasons and constraints."""
    details = [_detail("a", "Corner 1DK"), _detail("b", "Quiet 1K")]
    provider = SequenceProvider([_ranked_json()])
    recommender = Recommender(provider)
    result = recommender.recommend(details, _plan(), top_y=2)
    assert [r.rank for r in result] == [1, 2]
    assert result[0].listing_id == "a"
    assert result[0].satisfied_constraints == ["near station"]
    assert result[0].violated_constraints == []
    assert result[1].violated_constraints == ["pet friendly"]


def test_probable_negatives_carried_from_detail() -> None:
    """Caveats flow from the detail's probable_negatives into the recommendation."""
    details = [_detail("a", "Corner 1DK")]
    provider = SequenceProvider([_ranked_json()])
    recommender = Recommender(provider)
    result = recommender.recommend(details, _plan(), top_y=1)
    assert result[0].probable_negatives == ["pet consultation"]
    # The embedded listing data is carried for downstream reporting.
    assert result[0].listing is not None
    assert result[0].listing.internal_id == "a"


def test_empty_details_return_empty() -> None:
    """No details yield no recommendations without calling the provider."""
    provider = SequenceProvider(['{"ranked": []}'])
    recommender = Recommender(provider)
    assert recommender.recommend([], _plan()) == []
    assert provider.calls == 0


def test_markdown_report_matches_golden() -> None:
    """The markdown renderer matches the committed golden file."""
    details = [_detail("a", "Corner 1DK"), _detail("b", "Quiet 1K")]
    recommender = Recommender(SequenceProvider([_ranked_json()]))
    recs = recommender.recommend(details, _plan(), top_y=2)
    golden = (FIXTURES / "report.md").read_text(encoding="utf-8")
    assert render_markdown(recs, query="1DK near station") == golden


def test_json_report_matches_golden() -> None:
    """The JSON renderer matches the committed golden file and parses."""
    details = [_detail("a", "Corner 1DK"), _detail("b", "Quiet 1K")]
    recommender = Recommender(SequenceProvider([_ranked_json()]))
    recs = recommender.recommend(details, _plan(), top_y=2)
    rendered = render_json(recs)
    golden = (FIXTURES / "report.json").read_text(encoding="utf-8")
    assert rendered == golden
    # The golden output must be valid, deterministic JSON.
    parsed = json.loads(rendered)
    assert len(parsed["recommendations"]) == 2
