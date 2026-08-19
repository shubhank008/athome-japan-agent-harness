"""OpenRouter LLM transport (milestone M4, T18).

Concrete :class:`BaseLLMProvider` that talks to the OpenRouter chat
completions endpoint. It is the single module in the LLM layer allowed to
import a third-party HTTP client, per the Abstract First invariant.

The transport uses ``curl-cffi`` (the repository's actual HTTP dependency,
exact-pinned in ``requirements.txt``) rather than the httpx named by stale
plan wording, so no new dependency is introduced. The client is injectable for
unit tests (mirroring ``HttpDomAdapter.client``): production builds a
``curl_requests.Session``, tests pass a fake session exposing the narrow
:class:`ChatSession` surface.

Request behavior:

* model is configurable (defaults to the repository general model);
* temperature defaults to 0 for deterministic scoring;
* ``response_format`` requests JSON mode on the endpoint;
* the API key is taken from the environment, never logged;
* usage (prompt + completion tokens) is read off the response and returned as
  :class:`LLMUsage`.

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

# OpenRouter chat completions endpoint.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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
    """Minimal curl-cffi session surface used by the OpenRouter transport."""

    def post(self, url: str, **kwargs: object) -> ChatResponse:
        """Perform one POST request."""
        ...

    def close(self) -> None:
        """Release the session resources."""
        ...


class OpenRouterProvider(BaseLLMProvider):
    """``BaseLLMProvider`` over the OpenRouter chat completions API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_GENERAL_MODEL,
        session: ChatSession | None = None,
        base_url: str = OPENROUTER_URL,
    ) -> None:
        """Configure an OpenRouter transport.

        ``api_key`` is required and used only as an ``Authorization`` header.
        ``model`` selects the completion model. ``session`` allows tests to
        inject a fake transport; when omitted a real curl-cffi session is
        built and owned by this instance.
        """
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not resolved_key:
            raise LLMProviderError("OpenRouter API key is required")
        self._model = model
        self._base_url = base_url
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
        payload = {
            "model": self._model,
            "messages": [
                {"role": _SYSTEM_ROLE, "content": system},
                {"role": _USER_ROLE, "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._session.post(self._base_url, json=payload, headers=headers)
        except Exception as exc:  # transport-level failure (network, DNS, TLS)
            raise LLMProviderError(f"OpenRouter transport error: {type(exc).__name__}") from exc
        if not 200 <= response.status_code < 300:
            # API errors often carry a JSON body; surface status only, never body
            # which may embed secrets.
            raise LLMProviderError(f"OpenRouter returned HTTP {response.status_code}")
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
            raise LLMProviderError("OpenRouter returned a malformed response body") from exc
        if not isinstance(raw, dict):
            raise LLMProviderError("OpenRouter returned a non-object response body")
        return raw

    @staticmethod
    def _extract_content(body: dict[str, object]) -> str:
        """Extract the assistant message content from the completion body."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProviderError("OpenRouter response missing assistant content")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMProviderError("OpenRouter response missing assistant content")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError("OpenRouter response missing assistant content")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProviderError("OpenRouter response missing assistant content")
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
            raise LLMProviderError("OpenRouter response contained invalid token usage") from exc
