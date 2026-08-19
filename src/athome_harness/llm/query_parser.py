"""Natural-language query parser (milestone M4, T19).

Turns a free-text housing wish into a :class:`SearchPlan` by asking the LLM to
emit a structured intent, then resolving that intent against the versioned
filter map. Design decisions:

* The filter map is summarized into the prompt so the model sees the real
  option labels it can select (and their codes for later encoding).
* The model emits hard filters as ``{filter name: [option labels]}``. The
  resolver maps each label back to its code via the map for the chosen flow;
  any label or filter name that does not resolve is demoted to a soft
  preference rather than dropped, matching the "never silently lost" rule.
* Ambiguous queries set ``ambiguous=True`` plus a human question; the parser
  raises :class:`ClarificationNeeded` instead of guessing (US-001).

Rent-versus-buy intent is decided by the model through the ``flow`` field and
the summary is built per the chosen flow, so intent and code space always agree.

This module is pure orchestration over :class:`BaseLLMProvider`; it performs no
network I/O of its own and is fully testable with a canned provider.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from athome_harness.llm.base import BaseLLMProvider
from athome_harness.models import FilterMap, SearchPlan

logger = logging.getLogger(__name__)


class ClarificationNeeded(Exception):
    """Raised when a query is ambiguous and a clarifying question is required.

    ``question`` is a human-readable question to present to the user.
    """

    def __init__(self, question: str) -> None:
        self.question = question
        super().__init__(question)


class ParserOutput(BaseModel):
    """Structured intent the LLM emits before resolution to a SearchPlan.

    ``hard_filters`` maps a filter name to a list of *option labels* selected
    from the summarized map (codes are resolved later so this layer can also
    demote anything that does not resolve cleanly).
    """

    flow: Literal["rent", "buy"] = Field(description="Rental or purchase flow intent.")
    prefecture: str = Field(description="Target prefecture, e.g. osaka.")
    cities: list[str] = Field(
        default_factory=list, description="Target cities within the prefecture."
    )
    ambiguous: bool = Field(
        default=False, description="True when the query needs a clarifying question."
    )
    clarification_question: str | None = Field(
        default=None, description="Question to ask when ambiguous is true."
    )
    hard_filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Filter name to selected option labels (from the map summary).",
    )
    soft_prefs: list[str] = Field(
        default_factory=list,
        description="Natural-language preferences kept for LLM soft scoring.",
    )


# Instruction preamble shared across queries. The filter summary is appended.
_SYSTEM_PROMPT = (
    "You translate Japanese or English natural-language housing wishes into a "
    "structured search intent for the athome.co.jp filters. Follow the option "
    "labels and codes in the filter map summary exactly. Place any constraint "
    "that has no exact filter option under soft_prefs so it is never lost. "
    "If the query is genuinely ambiguous (e.g. no clear area or more than one "
    "possible interpretation), set ambiguous true and give a clarifying question."
)


def build_filter_map_summary(filter_map: FilterMap, flow: str) -> str:
    """Build the compact per-flow filter map text loaded into the prompt.

    Lists each filter's options as ``label (code)`` so the model can reference
    both the human label it selects and the code the encoder needs later.
    """
    lines: list[str] = []
    fields = filter_map.mappings.get(flow, {})
    if not fields:
        return f"(no filters available for flow {flow})"
    for field_name, options in fields.items():
        rendered = ", ".join(f"{opt.label} ({opt.code})" for opt in options)
        lines.append(f"{field_name}: {rendered}")
    return "\n".join(lines)


class QueryParser:
    """Parses a natural-language query into a :class:`SearchPlan`.

    The constructor takes the :class:`BaseLLMProvider` to call and the versioned
    :class:`FilterMap` used both for the prompt summary and code resolution.
    """

    def __init__(self, provider: BaseLLMProvider, filter_map: FilterMap) -> None:
        self._provider = provider
        self._filter_map = filter_map

    def parse(self, query: str, *, temperature: float = 0.0) -> SearchPlan:
        """Parse ``query`` into a SearchPlan, or raise ClarificationNeeded.

        Hard filters with labels that do not resolve against the chosen flow's
        map are demoted to soft preferences so nothing is silently dropped.
        """
        # Detect the flow first so the summary matches the intent's code space.
        flow = _detect_flow(self._provider, query, temperature=temperature)
        summary = build_filter_map_summary(self._filter_map, flow)
        system = _SYSTEM_PROMPT + "\n\nFilter map summary:\n" + summary
        output, usage = self._provider.complete_json(
            system=system,
            user=f"Query: {query}\n\nReturn the search intent as JSON.",
            schema=ParserOutput,
            temperature=temperature,
        )
        logger.debug(
            "[PARSE_TOKENS] prompt=%d completion=%d flow=%s",
            usage.prompt_tokens,
            usage.completion_tokens,
            output.flow,
        )
        if output.ambiguous:
            raise ClarificationNeeded(
                output.clarification_question or "Please clarify your search intent."
            )
        if output.flow != flow:
            logger.warning(
                "LLM flow changed from detected %s to %s; using detected flow",
                flow,
                output.flow,
            )
        return _resolve_plan(output, self._filter_map, flow=flow)


def _detect_flow(
    provider: BaseLLMProvider, query: str, temperature: float
) -> Literal["rent", "buy"]:
    """Ask the model which flow (rent vs buy) the query targets.

    A dedicated minimal call keeps the code-space decision independent from the
    full intent parse so the two can disagree without corrupting each other.
    """

    class _Flow(BaseModel):
        flow: Literal["rent", "buy"]

    flow_out, _ = provider.complete_json(
        system="Return the search flow: rent for rental listings, buy for purchase listings.",
        user=f"Query: {query}\n\nReturn JSON with a single field 'flow'.",
        schema=_Flow,
        temperature=temperature,
    )
    return flow_out.flow


def _resolve_plan(
    output: ParserOutput,
    filter_map: FilterMap,
    *,
    flow: Literal["rent", "buy"] | None = None,
) -> SearchPlan:
    """Resolve labeled hard filters to codes, demoting anything unmappable.

    For each filter name the resolver looks up the detected flow's options.
    Unknown filter names and any label that does not match an option are moved
    into ``soft_prefs`` (prefixed with the filter name for context), preserving
    the information the encoder cannot represent as hard params.
    """
    resolved_flow = flow or output.flow
    hard: dict[str, list[str]] = {}
    soft: list[str] = list(output.soft_prefs)
    fields = filter_map.mappings.get(resolved_flow, {})
    for field_name, labels in output.hard_filters.items():
        options = fields.get(field_name)
        resolved: list[str] = []
        for label in labels:
            if options is None:
                soft.append(f"{field_name}: {label}")
                continue
            code = next((opt.code for opt in options if opt.label == label), None)
            if code is not None:
                resolved.append(code)
            else:
                soft.append(f"{field_name}: {label}")
        if resolved:
            hard[field_name] = resolved
    return SearchPlan(
        flow=resolved_flow,
        prefecture=output.prefecture,
        cities=output.cities,
        hard_filters=hard,
        soft_prefs=soft,
    )
