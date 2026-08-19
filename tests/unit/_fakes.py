"""Shared in-memory test doubles for the LLM layer (M4).

Reused by the query parser, shortlister, and recommender unit tests so canned
LLM behavior is defined once. No network is used.
"""

from __future__ import annotations

from pathlib import Path

from athome_harness.filters.map_schema import validate
from athome_harness.llm.base import BaseLLMProvider, LLMUsage
from athome_harness.models import FilterMap

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class SequenceProvider(BaseLLMProvider):
    """Serves a queue of canned JSON completions, one per call, in order.

    The last reply repeats if the provider is called more times than replies,
    so a happy-path call that under-uses the queue still works deterministically.
    """

    def __init__(self, replies: list[str], *, usage: LLMUsage | None = None) -> None:
        self.replies = list(replies)
        self.calls = 0
        self._usage = usage or LLMUsage(prompt_tokens=4, completion_tokens=2)

    def complete_text(
        self, *, system: str, user: str, temperature: float = 0.0
    ) -> tuple[str, LLMUsage]:
        self.calls += 1
        index = min(self.calls - 1, len(self.replies) - 1)
        return self.replies[index], self._usage


def build_filter_map() -> FilterMap:
    """Build and validate the filter map from the deterministic HTML fixtures."""
    from tools.dump_filter_map import extract_flow

    rent = extract_flow((FIXTURES / "filter_map_rent.html").read_text(encoding="utf-8"), "rent")
    buy = extract_flow((FIXTURES / "filter_map_buy.html").read_text(encoding="utf-8"), "buy")
    filter_map = FilterMap(
        version=1,
        content_hash="0" * 12,
        mappings={"rent": rent, "buy": buy},
    )
    return validate(filter_map)
