"""Unit tests for the OpenRouter transport (M4, T18).

Exercises the provider with an injected fake session so no network is used.
Verifies the request payload (model, temperature default, JSON mode), the
Authorization header, token usage accounting, and safe error handling for
HTTP and malformed-body failures.
"""

from __future__ import annotations

import pytest

from athome_harness.llm.base import LLMProviderError, LLMUsage
from athome_harness.llm.openrouter import OpenRouterProvider


class FakeResponse:
    """A minimal ChatResponse stub."""

    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """A minimal ChatSession stub that records the last request."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        if self._responses:
            return self._responses.pop(0)
        raise AssertionError("fake session exhausted its responses")

    def close(self) -> None:
        self.closed = True


def _ok_body(content: str = '{"flow": "rent"}') -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


def test_uses_injected_session_no_network() -> None:
    """The provider drives the injected session and returns parsed content."""
    session = FakeSession([FakeResponse(200, _ok_body())])
    provider = OpenRouterProvider("test-key", session=session)
    text, usage = provider.complete_text(system="sys", user="usr")
    assert text == '{"flow": "rent"}'
    assert usage == LLMUsage(prompt_tokens=12, completion_tokens=7)
    assert len(session.calls) == 1


def test_request_payload_model_temperature_json_mode() -> None:
    """Payload carries the configured model, temperature 0, and JSON mode."""
    session = FakeSession([FakeResponse(200, _ok_body())])
    provider = OpenRouterProvider("test-key", model="deepseek/x", session=session)
    provider.complete_text(system="sys", user="usr")
    _, kwargs = session.calls[0]
    payload = kwargs["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek/x"
    assert payload["temperature"] == 0.0
    assert payload["response_format"] == {"type": "json_object"}
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "usr"


def test_authorization_header_carries_key() -> None:
    """The OpenRouter key is sent as a Bearer Authorization header only."""
    session = FakeSession([FakeResponse(200, _ok_body())])
    provider = OpenRouterProvider("super-secret", session=session)
    provider.complete_text(system="sys", user="usr")
    _, kwargs = session.calls[0]
    headers = kwargs["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer super-secret"


def test_http_error_raises_provider_error() -> None:
    """A non-2xx response raises LLMProviderError with no body detail."""
    session = FakeSession([FakeResponse(500, {"error": {"message": "top secret"}})])
    provider = OpenRouterProvider("k", session=session)
    with pytest.raises(LLMProviderError):
        provider.complete_text(system="sys", user="usr")


def test_malformed_body_raises_provider_error() -> None:
    """A non-dict or incomplete body raises a typed error."""
    session = FakeSession([FakeResponse(200, "not a dict")])
    provider = OpenRouterProvider("k", session=session)
    with pytest.raises(LLMProviderError):
        provider.complete_text(system="sys", user="usr")


def test_missing_content_raises_provider_error() -> None:
    """A body without assistant content raises a typed error."""
    session = FakeSession([FakeResponse(200, {"choices": []})])
    provider = OpenRouterProvider("k", session=session)
    with pytest.raises(LLMProviderError):
        provider.complete_text(system="sys", user="usr")


def test_transport_error_raises_provider_error() -> None:
    """A session-level exception is wrapped as LLMProviderError."""

    class _Boom:
        def post(self, url: str, **kwargs: object) -> FakeResponse:
            raise OSError("connection refused")

        def close(self) -> None:
            pass

    provider = OpenRouterProvider("k", session=_Boom())  # type: ignore[arg-type]
    with pytest.raises(LLMProviderError):
        provider.complete_text(system="sys", user="usr")


def test_temperature_can_be_configured() -> None:
    """Temperature reflects the passed value rather than always defaulting."""
    session = FakeSession([FakeResponse(200, _ok_body())])
    provider = OpenRouterProvider("k", session=session)
    provider.complete_text(system="sys", user="usr", temperature=0.7)
    _, kwargs = session.calls[0]
    assert kwargs["json"]["temperature"] == 0.7  # type: ignore[index]
