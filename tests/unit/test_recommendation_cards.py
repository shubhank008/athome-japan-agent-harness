"""Fixture-backed tests for recommendation-card ingestion."""

from pathlib import Path

from athome_harness.models import ListingCompleteness, ListingDetail, PriceBreakdown
from athome_harness.scraping.recommendation_cards import ingest_recommendation_cards
from athome_harness.scraping.server_app_state import extract_server_app_recommendation_cards
from athome_harness.store.sqlite_store import SqliteStore

_PAYLOAD = Path("data/server-app-state-1106831830.full.json").read_text()


def _html() -> str:
    """Wrap the representative state fixture in the parser's script contract."""
    return f'<script id="serverApp-state">{_PAYLOAD}</script>'


def test_cards_normalize_and_ingest_as_complete_summaries() -> None:
    """All representative cards map to complete, useful summaries."""
    cards = extract_server_app_recommendation_cards(_html(), "1106831830")
    store = SqliteStore(":memory:")
    result = ingest_recommendation_cards(cards, store)
    assert len(result.summaries) == 20
    assert len({summary.athome_key for summary in result.summaries}) == 20
    first = result.summaries[0]
    assert first.completeness is ListingCompleteness.SUMMARY_COMPLETE
    assert first.athome_key == "1106827130"
    assert first.url.endswith("/chintai/1106827130/")
    assert first.price.rent == 78_000
    assert first.price.management_fee == 7_200
    assert first.area_m2 == 21.28
    assert first.station == "谷町六丁目"
    assert first.walk_minutes == 6
    assert first.photo_urls
    assert first.source_data["id"] == "1106827130"


def test_repeated_ingestion_is_idempotent() -> None:
    """Repeated cards update one row per AtHome identifier."""
    cards = extract_server_app_recommendation_cards(_html(), "1106831830")
    store = SqliteStore(":memory:")
    ingest_recommendation_cards(cards, store)
    ingest_recommendation_cards(cards, store)
    assert len(store.list_listings()) == 20


def test_card_does_not_downgrade_detail_or_erase_fields() -> None:
    """A card cannot replace richer detail fields or complete agency data."""
    cards = extract_server_app_recommendation_cards(_html(), "1106831830")
    store = SqliteStore(":memory:")
    detail = ListingDetail(
        internal_id=cards[0].athome_key,
        athome_key=cards[0].athome_key,
        url="https://www.athome.co.jp/chintai/1106827130/",
        title="Detailed title",
        address="Detailed address",
        price=PriceBreakdown(rent=99_000),
        area_m2=30,
        description="Rich detail",
        listing_detail=True,
    )
    store.upsert_listing(detail)
    ingest_recommendation_cards(cards[:1], store)
    saved = store.get_listing(detail.internal_id)
    assert isinstance(saved, ListingDetail)
    assert saved.completeness is ListingCompleteness.DETAIL_COMPLETE
    assert saved.description == "Rich detail"
    assert saved.price.rent == 99_000
    assert saved.address == "Detailed address"


def test_optional_card_fields_are_tolerated() -> None:
    """Cards with missing nested optional values still normalize safely."""
    cards = extract_server_app_recommendation_cards(_html(), "1106831830")
    cards[0].raw.pop("traffic")
    cards[0].raw.pop("images")
    cards[0].raw.pop("mainImage")
    cards[0].raw.pop("contract")
    cards[0].location = ""
    summary = ingest_recommendation_cards(cards[:1], SqliteStore(":memory:")).summaries[0]
    assert summary.walk_minutes is None
    assert summary.photo_urls == []
    assert summary.price.rent == 0
