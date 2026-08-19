"""AtHome harness LLM layer: provider abstraction and the plan/shortlist/report.

Import surface: consumers depend on :class:`BaseLLMProvider` / the funnel
classes and never import a third-party LLM SDK directly. The concrete
:class:`OpenRouterProvider` transport is exposed here for composition at the
CLI orchestration layer.
"""

from athome_harness.llm.base import (
    BaseLLMProvider,
    LLMJSONInvalidError,
    LLMProviderError,
    LLMUsage,
)
from athome_harness.llm.openrouter import OpenRouterProvider
from athome_harness.llm.query_parser import ClarificationNeeded, QueryParser
from athome_harness.llm.recommender import (
    RecommendationOutput,
    Recommender,
    render_json,
    render_markdown,
)
from athome_harness.llm.shortlister import ShortlistEntry, Shortlister

__all__ = [
    "BaseLLMProvider",
    "ClarificationNeeded",
    "LLMJSONInvalidError",
    "LLMProviderError",
    "LLMUsage",
    "OpenRouterProvider",
    "QueryParser",
    "RecommendationOutput",
    "Recommender",
    "ShortlistEntry",
    "Shortlister",
    "render_json",
    "render_markdown",
]
