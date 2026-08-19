"""Unit tests for the shortlister (M4, T20).

Verifies ordered top-X scoring against soft preferences, determinism (same
input, same output), and token-budget enforcement with a canned provider.
"""

from __future__ import annotations

from athome_harness.llm.shortlister import ShortlistEntry, Shortlister
from athome_harness.models import ListingSummary, PriceBreakdown
from tests.unit._fakes import SequenceProvider


def _listing(internal_id: str, title: str) -> ListingSummary:
    """Build a minimal ListingSummary with only required fields."""
    return ListingSummary(
        internal_id=internal_id,
        athome_key=internal_id,
        url=f"https://e.test/{internal_id}",
        title=title,
        address="osaka",
        price=PriceBreakdown(rent=100_000),
        area_m2=40.0,
    )


def _batch_json(entries: list[tuple[str, float, str]]) -> str:
    body = {
        "entries": [
            {"listing_id": lid, "score": score, "rationale": rationale}
            for lid, score, rationale in entries
        ]
    }
    return __import__("json").dumps(body)


def test_orders_top_x_by_score() -> None:
    """Results are sorted by score descending and truncated to top_x."""
    listings = [_listing("a", "A"), _listing("b", "B"), _listing("c", "C")]
    reply = _batch_json(
        [
            ("a", 5.0, "ok"),
            ("b", 9.5, "great"),
            ("c", 7.0, "fine"),
        ]
    )
    provider = SequenceProvider([reply])
    shortlister = Shortlister(provider)
    result = shortlister.shortlist(["near station"], listings, top_x=2)
    assert [e.listing_id for e in result] == ["b", "c"]
    assert result[0].rationale == "great"
    assert isinstance(result[0], ShortlistEntry)


def test_deterministic_same_input_same_output() -> None:
    """Two identical runs over identical inputs produce identical results."""
    listings = [_listing("a", "A"), _listing("b", "B")]
    reply = _batch_json([("a", 6.0, "x"), ("b", 8.0, "y")])
    shortlister = Shortlister(SequenceProvider([reply]))
    first = shortlister.shortlist(["pref"], listings)
    second = shortlister.shortlist(["pref"], listings)
    # The provider re-serves the same canned reply for each run.
    assert first == second


def test_token_budget_splits_into_multiple_batches() -> None:
    """A tight token budget forces batching but never drops a listing."""
    listings = [_listing("a", "A"), _listing("b", "B")]
    # max_batch_tokens=1 with chars_per_token=4 means each listing (whose
    # serialized form exceeds 4 chars) becomes its own batch, so the provider
    # is asked once per batch and each reply covers only that batch's listing.
    provider = SequenceProvider([_batch_json([("a", 6.0, "x")]), _batch_json([("b", 8.0, "y")])])
    shortlister = Shortlister(provider, chars_per_token=4, max_batch_tokens=1)
    result = shortlister.shortlist(["pref"], listings)
    assert provider.calls >= 2, "expected more than one scoring batch"
    # Every listing still appears in the merged output.
    assert {e.listing_id for e in result} == {"a", "b"}


def test_empty_listings_return_empty() -> None:
    """No listings yields an empty shortlist without calling the provider."""
    provider = SequenceProvider(['{"entries": []}'])
    shortlister = Shortlister(provider)
    assert shortlister.shortlist(["pref"], []) == []
    assert provider.calls == 0
