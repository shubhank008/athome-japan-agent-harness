"""Shared OpenAI-compatible chat completions transport (milestone M8).

Both the OpenRouter and OpencodeGo providers talk to an OpenAI-compatible
``chat/completions`` endpoint with an identical wire contract (messages,
temperature, optional JSON mode, optional ``max_tokens``, token usage block).
This module factors that contract into one :class:`OpenAICompatibleProvider`
base so the concrete transports are thin label/URL declarations and never
duplicate parsing or error-handling logic.

It follows the repository's Abstract First invariant: this is the single place
in the LLM layer allowed to import a third-party HTTP client
(``curl-cffi``, exact-pinned in ``requirements.txt``). The session is
injectable for unit tests (mirroring ``HttpDomAdapter.client``): production
builds a ``curl_requests.Session``, tests pass a fake session exposing the
narrow :class:`ChatSession` surface.

Safe error handling: transport/HTTP errors and API-level error payloads raise
:class:`LLMProviderError` with no secret material. A body that cannot be parsed
as JSON also raises a typed error instead of leaking raw content.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, cast

from curl_cffi import requests as curl_requests

from athome_harness.config import DEFAULT_GENERAL_MODEL
from athome_harness.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage

logger = logging.getLogger(__name__)

# The chat system role label the endpoint expects.
_SYSTEM_ROLE = "system"
_USER_ROLE = "user"


class ChatResponse(Protocol):
    """Minimal response surface required from curl-cffi or a test stub."""

    @property
    def status_code(self) -> int:
        """Return the HTTP status code."""
        ...

    def json(self) -> object:
        """Return the parsed JSON body."""
        ...

    def raise_for_status(self) -> None:
        """Raise on a non-success HTTP status."""
        ...


class ChatSession(Protocol):
    """Minimal curl-cffi session surface used by the OpenAI-compatible transport."""

    def post(self, url: str, **kwargs: object) -> ChatResponse:
        """Perform one POST request."""
        ...

    def close(self) -> None:
        """Release the session resources."""
        ...


class OpenAICompatibleProvider(BaseLLMProvider):
    """``BaseLLMProvider`` over an OpenAI-compatible chat completions endpoint.

    Concrete subclasses supply the ``provider_name`` label (used in error
    messages and HTTP status labels) and the ``base_url`` of their endpoint.
    All request/response handling is shared here.
    """

    # Human-readable label used in error messages. Subclasses override.
    provider_name = "OpenAI-compatible"
    # Default endpoint URL. Subclasses override with their own endpoint.
    default_base_url = ""
    # Environment variable name that carries the API key. Subclasses override.
    env_api_key = ""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_GENERAL_MODEL,
        session: ChatSession | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Configure an OpenAI-compatible transport.

        ``api_key`` is required and used only as an ``Authorization`` header.
        ``model`` selects the completion model. ``max_tokens`` is the API-level
        ceiling on completion tokens (mapped from ``ATHOME_LLM_MAX_TOKENS``);
        when *None* the endpoint default is used. ``session`` allows tests to
        inject a fake transport; when omitted a real curl-cffi session is built
        and owned by this instance. ``base_url`` overrides the subclass default
        endpoint; when *None* the subclass ``default_base_url`` is used.
        """
        resolved_key = api_key or os.environ.get(self.env_api_key)
        if not resolved_key:
            raise LLMProviderError(f"{self.provider_name} API key is required")
        resolved_base_url = base_url or self.default_base_url
        if not resolved_base_url:
            raise LLMProviderError(f"{self.provider_name} base URL is required")
        self._model = model
        self._base_url = resolved_base_url
        self._max_tokens = max_tokens
        self._session_owned = session is None
        self._session: ChatSession = session or self._build_session()
        # Stored solely to seed the Authorization header; never logged.
        self._api_key = resolved_key

    def _build_session(self) -> ChatSession:
        """Build a curl-cffi session with a browser profile for the API host."""
        return cast(
            ChatSession,
            curl_requests.Session(
                impersonate="chrome",
                default_headers=False,
            ),
        )

    def close(self) -> None:
        """Release the session when this instance owns it."""
        if self._session_owned:
            self._session.close()

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
    ) -> tuple[str, LLMUsage]:
        """Return ``(text, usage)`` for a system/user message pair."""
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": _SYSTEM_ROLE, "content": system},
                {"role": _USER_ROLE, "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._session.post(self._base_url, json=payload, headers=headers)
        except Exception as exc:  # transport-level failure (network, DNS, TLS)
            raise LLMProviderError(
                f"{self.provider_name} transport error: {type(exc).__name__}"
            ) from exc
        if not 200 <= response.status_code < 300:
            # API errors often carry a JSON body; surface status only, never body
            # which may embed secrets.
            raise LLMProviderError(
                f"{self.provider_name} returned HTTP {response.status_code}"
            )
        body = self._parse_body(response)
        content = self._extract_content(body)
        usage = self._extract_usage(body)
        return content, usage

    @staticmethod
    def _parse_body(response: ChatResponse) -> dict[str, object]:
        """Parse the response body into a dict, raising on malformed JSON."""
        try:
            raw = response.json()
        except Exception as exc:
            raise LLMProviderError("response body could not be parsed as JSON") from exc
        if not isinstance(raw, dict):
            raise LLMProviderError("response body is not a JSON object")
        return raw

    @staticmethod
    def _extract_content(body: dict[str, object]) -> str:
        """Extract the assistant message content from the completion body."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProviderError("response missing assistant content")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMProviderError("response missing assistant content")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError("response missing assistant content")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProviderError("response missing assistant content")
        return content

    @staticmethod
    def _extract_usage(body: dict[str, object]) -> LLMUsage:
        """Read prompt/completion token counts from the usage block."""
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return LLMUsage()
        try:
            return LLMUsage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
        except (TypeError, ValueError) as exc:
            raise LLMProviderError("response contained invalid token usage") from exc
