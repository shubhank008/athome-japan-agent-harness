"""Unit tests for the OpencodeGo transport (M8).

Verifies the OpencodeGo-specific defaults (endpoint URL, model format, API-key
environment variable) over the shared :class:`OpenAICompatibleProvider` base.
The shared wire contract (payload shape, token accounting, error handling) is
already exercised through the OpenRouter tests and is not duplicated here.
"""

from __future__ import annotations

import pytest

from athome_harness.config import DEFAULT_OPENCODEGO_MODEL
from athome_harness.llm.base import LLMProviderError
from athome_harness.llm.opencodego import OPENCODEGO_URL, OpenCodeGoProvider


class _FakeResponse:
    """A minimal ChatResponse stub."""

    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        pass


class _FakeSession:
    """A minimal ChatSession stub that records the last request."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._responses.pop(0)

    def close(self) -> None:
        pass


def _ok_body() -> dict[str, object]:
    return {"choices": [{"message": {"content": "{}"}}], "usage": {}}


def test_defaults_endpoint_and_model() -> None:
    """Without overrides the endpoint and opencode-go model default are used."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("k", session=session)
    provider.complete_text(system="sys", user="usr")
    url, kwargs = session.calls[0]
    assert url == OPENCODEGO_URL
    assert kwargs["json"]["model"] == DEFAULT_OPENCODEGO_MODEL  # type: ignore[index]


def test_authorization_header_carries_key() -> None:
    """The OpencodeGo key is sent as a Bearer Authorization header only."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("go-secret", session=session)
    provider.complete_text(system="sys", user="usr")
    _, kwargs = session.calls[0]
    headers = kwargs["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer go-secret"


def test_coding_agent_headers_use_one_stable_session_id() -> None:
    """OpenCodeGo identifies the app and reuses one UUID per provider instance."""
    session = _FakeSession([_FakeResponse(200, _ok_body()), _FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("go-secret", session=session)

    provider.complete_text(system="sys", user="first")
    provider.complete_text(system="sys", user="second")

    first_headers = session.calls[0][1]["headers"]
    second_headers = session.calls[1][1]["headers"]
    assert isinstance(first_headers, dict)
    assert isinstance(second_headers, dict)
    assert first_headers["User-Agent"] == "athome-japan-agent-harness/0.1.0"
    assert first_headers["x-opencode-session"] == second_headers["x-opencode-session"]


def test_provider_instances_use_distinct_session_ids() -> None:
    """Separate OpenCodeGo conversations must not share a routing session ID."""
    first_session = _FakeSession([_FakeResponse(200, _ok_body())])
    second_session = _FakeSession([_FakeResponse(200, _ok_body())])
    first_provider = OpenCodeGoProvider("k", session=first_session)
    second_provider = OpenCodeGoProvider("k", session=second_session)

    first_provider.complete_text(system="sys", user="usr")
    second_provider.complete_text(system="sys", user="usr")

    first_headers = first_session.calls[0][1]["headers"]
    second_headers = second_session.calls[0][1]["headers"]
    assert isinstance(first_headers, dict)
    assert isinstance(second_headers, dict)
    assert first_headers["x-opencode-session"] != second_headers["x-opencode-session"]


def test_returns_parsed_content() -> None:
    """Complete_text returns the parsed assistant content."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("k", session=session)
    text, _usage = provider.complete_text(system="sys", user="usr")
    assert text == "{}"


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key may be supplied by the OPENCODEGO_API_KEY environment variable."""
    monkeypatch.setenv("OPENCODEGO_API_KEY", "env-go-key")
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider(session=session)
    provider.complete_text(system="sys", user="usr")
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer env-go-key"  # type: ignore[index]


def test_missing_api_key_raises() -> None:
    """No key and no environment variable raises a typed provider error."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    with pytest.raises(LLMProviderError):
        OpenCodeGoProvider(session=session)


def test_unknown_provider_name_error_message() -> None:
    """Missing-key error identifies the OpencodeGo provider explicitly."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    with pytest.raises(LLMProviderError, match="OpenCodeGo"):
        OpenCodeGoProvider(session=session)


def test_universal_policy_payload_when_configured() -> None:
    """OpenCodeGo receives exactly the universal policy fields."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("k", session=session, max_tokens=513, reasoning_effort="high")
    provider.complete_text(system="sys", user="usr")
    payload = session.calls[0][1]["json"]
    assert payload["max_tokens"] == 513  # type: ignore[index]
    assert payload["max_completion_tokens"] == 513  # type: ignore[index]
    assert payload["reasoning_effort"] == "high"  # type: ignore[index]
    assert "max_output_tokens" not in payload  # type: ignore[operator]
    assert "reasoning" not in payload  # type: ignore[operator]


def test_unset_token_budget_omits_policy_fields() -> None:
    """An unset token budget omits both ceilings and reasoning effort."""
    session = _FakeSession([_FakeResponse(200, _ok_body())])
    provider = OpenCodeGoProvider("k", session=session)
    provider.complete_text(system="sys", user="usr")
    payload = session.calls[0][1]["json"]
    assert "max_tokens" not in payload  # type: ignore[operator]
    assert "max_completion_tokens" not in payload  # type: ignore[operator]
    assert "reasoning_effort" not in payload  # type: ignore[operator]
    assert "max_output_tokens" not in payload  # type: ignore[operator]
    assert "reasoning" not in payload  # type: ignore[operator]
