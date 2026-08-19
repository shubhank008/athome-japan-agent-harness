"""Contract tests for BaseLLMProvider (M4, T17).

Proves the schema-validated ``complete_json`` loop, token accounting, the
exactly-one repair retry, and the typed failure path, all against a
deterministic in-memory fake provider with no network access.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from athome_harness.llm.base import (
    BaseLLMProvider,
    LLMJSONInvalidError,
    LLMUsage,
)


class SampleSchema(BaseModel):
    """A tiny schema used to exercise complete_json validation."""

    name: str = Field(description="A name.")
    value: int = Field(ge=0, description="A non-negative value.")


class FakeProvider(BaseLLMProvider):
    """Deterministic provider that serves a queue of canned completions.

    Each ``complete_text`` call pops the next string from ``replies`` (cycling
    the last one) and returns a fixed usage so accounting is assertable.
    """

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.users: list[str] = []
        self.usage = LLMUsage(prompt_tokens=10, completion_tokens=5)

    @property
    def last_user(self) -> str:
        """Return the user prompt sent for the latest completion."""
        return self.users[-1]

    def complete_text(
        self, *, system: str, user: str, temperature: float = 0.0
    ) -> tuple[str, LLMUsage]:
        self.calls += 1
        self.users.append(user)
        if len(self.replies) == 0:
            raise AssertionError("fake provider exhausted its replies")
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return text, self.usage


def _ok_json() -> str:
    return '{"name": "ok", "value": 3}'


def test_fake_provider_implements_contract() -> None:
    """A subclass of BaseLLMProvider with complete_text is usable."""

    def use(p: BaseLLMProvider) -> None:
        text, usage = p.complete_text(system="s", user="u")
        assert text == _ok_json()
        assert usage.total == 15

    use(FakeProvider([_ok_json()]))


def test_complete_json_validates_schema() -> None:
    """A well-formed completion parses and validates against the schema."""
    provider = FakeProvider([_ok_json()])
    model, usage = provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert model.name == "ok"
    assert model.value == 3
    assert usage.total == 15
    assert provider.calls == 1


def test_complete_json_tolerates_prose_wrapped_json() -> None:
    """Prose around a JSON object is stripped and still validates."""
    provider = FakeProvider(['Here is the answer: {"name": "x", "value": 1} done.'])
    model, _ = provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert model.name == "x"
    assert provider.calls == 1


def test_complete_json_repairs_once_then_succeeds() -> None:
    """Invalid JSON triggers exactly one repair retry that recovers."""
    provider = FakeProvider(["not json", _ok_json()])
    model, usage = provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert model.name == "ok"
    # The original attempt plus exactly one repair.
    assert provider.calls == 2
    # Prompt tokens counted twice (both calls), completion twice.
    assert usage.total == 30
    assert provider.total_tokens == 30


def test_repair_prompt_keeps_request_and_invalid_completion() -> None:
    """Repair requests retain both task context and the rejected completion."""
    provider = FakeProvider(["not json", _ok_json()])
    provider.complete_json(system="s", user="original request", schema=SampleSchema)
    assert "original request" in provider.last_user
    assert "not json" in provider.last_user


def test_complete_json_fails_after_one_repair(caplog: pytest.LogCaptureFixture) -> None:
    """Two invalid responses raise once with the terminal failure marker."""
    provider = FakeProvider(["bad", "also bad"])
    with caplog.at_level("WARNING"):
        with pytest.raises(LLMJSONInvalidError):
            provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert provider.calls == 2
    records = [record.getMessage() for record in caplog.records]
    assert sum("LLM_JSON_INVALID" in record for record in records) == 1


def test_repairable_failure_has_no_invalid_marker(caplog: pytest.LogCaptureFixture) -> None:
    """A successful repair must not emit the terminal failure marker."""
    provider = FakeProvider(["bad", _ok_json()])
    with caplog.at_level("WARNING"):
        provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert not any("LLM_JSON_INVALID" in record.getMessage() for record in caplog.records)


def test_complete_json_schema_mismatch_triggers_repair() -> None:
    """A JSON object that fails schema validation also triggers the repair."""
    # value must be >= 0; a negative value fails the validator.
    provider = FakeProvider(['{"name": "x", "value": -5}', '{"name": "y", "value": 2}'])
    model, _ = provider.complete_json(system="s", user="u", schema=SampleSchema)
    assert model.name == "y"
    assert provider.calls == 2
