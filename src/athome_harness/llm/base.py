"""LLM provider abstraction and JSON completion contract (milestone M4, T17).

This module owns the business-facing LLM contract (:class:`BaseLLMProvider`),
the token accounting value object (:class:`LLMUsage`), and the typed errors the
rest of the funnel raises. It follows the repository's Abstract First
invariant: only the standard library and project interfaces are imported here.
The concrete transport (:class:`OpenRouterProvider` in ``openrouter.py``) is the
only place a third-party HTTP client may appear.

The shared :meth:`BaseLLMProvider.complete_json` method implements the
schema-validated completion loop once so every consumer (query parser,
shortlister, recommender) gets identical behavior: parse the raw completion,
validate it against a pydantic schema, and if that fails perform exactly one
repair retry asking the model for valid JSON. Token usage across both the real
attempt and the repair is summed into the returned :class:`LLMUsage`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# Contract failure pattern: emitted only when both the original completion and
# the single repair attempt fail to produce schema-valid JSON. It must never
# appear on a happy-path run (see contracts/log-markers.md).
LLM_JSON_INVALID_MARKER = "LLM_JSON_INVALID"


class LLMUsage(BaseModel):
    """Token accounting for one or more LLM calls.

    ``prompt_tokens`` counts the request side, ``completion_tokens`` the
    response side. ``total`` is their sum and is what a session report consumes.
    """

    prompt_tokens: int = Field(default=0, ge=0, description="Prompt-side tokens.")
    completion_tokens: int = Field(default=0, ge=0, description="Completion-side tokens.")

    @property
    def total(self) -> int:
        """Total tokens consumed across prompt and completion."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_tokens(self) -> int:
        """Compatibility alias for report and budget consumers."""
        return self.total


class LLMProviderError(RuntimeError):
    """Raised when the transport fails (transport, HTTP, or API-level error)."""


class LLMJSONInvalidError(LLMProviderError):
    """Raised when a complete_json call cannot produce schema-valid JSON.

    This fires only after the original completion and the single repair retry
    both fail, so consumers can treat it as a terminal outcome and log the
    ``LLM_JSON_INVALID`` failure marker.
    """


def _extract_json[SchemaT: BaseModel](text: str, schema: type[SchemaT]) -> SchemaT:
    """Parse ``text`` as JSON and validate it against ``schema``.

    Models sometimes wrap an otherwise valid object in prose or markdown. The
    decoder extracts the first complete object without accidentally swallowing
    a later brace from trailing text.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as direct_error:
        start = text.find("{")
        if start == -1:
            raise direct_error from None
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            raise direct_error from None
    return schema.model_validate(payload)


def _repair_prompt[SchemaT: BaseModel](
    original_user: str, invalid_completion: str, schema: type[SchemaT]
) -> str:
    """Build the repair instruction sent for the single retry.

    Includes both the original request and the invalid completion so the model
    can correct the response without losing the task context.
    """
    return (
        "Your previous completion was not valid JSON matching the required schema. "
        f"The schema is:\n{schema.model_json_schema()}\n"
        "Return ONLY valid JSON for that schema, with no surrounding text.\n"
        f"Original request:\n{original_user[:2000]}\n"
        f"Your previous (invalid) answer was:\n{invalid_completion[:2000]}"
    )


class BaseLLMProvider(ABC):
    """Abstract LLM contract implemented by concrete transports.

    Subclasses implement the raw :meth:`complete_text` primitive. All consumers
    call :meth:`complete_json`, which layers schema validation, token
    accounting, and the exactly-one repair retry on top of the primitive.
    """

    @property
    def total_usage(self) -> LLMUsage:
        """Return cumulative usage recorded by schema-completion calls."""
        return getattr(self, "_total_usage", LLMUsage())

    @property
    def total_tokens(self) -> int:
        """Return cumulative prompt and completion tokens."""
        return self.total_usage.total

    def _record_usage(self, usage: LLMUsage) -> None:
        """Add one completion's usage to the provider session total."""
        current = self.total_usage
        self._total_usage = LLMUsage(
            prompt_tokens=current.prompt_tokens + usage.prompt_tokens,
            completion_tokens=current.completion_tokens + usage.completion_tokens,
        )

    @abstractmethod
    def complete_text(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> tuple[str, LLMUsage]:
        """Return ``(raw text, usage)`` for a system/user message pair.

        ``temperature`` defaults to 0 for deterministic scoring (SPEC section 5).
        """

    def complete_json[SchemaT: BaseModel](
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        temperature: float = 0.0,
    ) -> tuple[SchemaT, LLMUsage]:
        """Return a schema-validated ``schema`` instance plus total usage.

        The raw completion is parsed and validated against ``schema``. On
        failure exactly one repair retry runs with an explicit instruction to
        return valid JSON. If both attempts fail, :class:`LLMJSONInvalidError`
        is raised and the ``LLM_JSON_INVALID`` failure marker is logged.
        Prompt and completion tokens are summed across the original call and,
        when it happens, the repair call.
        """
        text, usage = self.complete_text(system=system, user=user, temperature=temperature)
        self._record_usage(usage)
        try:
            return _extract_json(text, schema), usage
        except (ValidationError, json.JSONDecodeError) as first_error:
            logger.debug("first JSON parse failed: %s", type(first_error).__name__)
        # Exactly one repair retry.
        repaired, repair_usage = self.complete_text(
            system=system,
            user=_repair_prompt(user, text, schema),
            temperature=temperature,
        )
        self._record_usage(repair_usage)
        merged_usage = LLMUsage(
            prompt_tokens=usage.prompt_tokens + repair_usage.prompt_tokens,
            completion_tokens=usage.completion_tokens + repair_usage.completion_tokens,
        )
        try:
            return _extract_json(repaired, schema), merged_usage
        except (ValidationError, json.JSONDecodeError) as second_error:
            logger.error(
                "[%s] JSON invalid after repair: %s",
                LLM_JSON_INVALID_MARKER,
                type(second_error).__name__,
            )
            raise LLMJSONInvalidError(
                f"LLM produced no schema-valid JSON after a repair retry (schema={schema.__name__})"
            ) from second_error
