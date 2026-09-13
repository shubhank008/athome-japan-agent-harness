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

import json
import logging
import os
import time
from pathlib import Path
from typing import Protocol, cast

from curl_cffi import requests as curl_requests

from athome_harness.config import DEFAULT_GENERAL_MODEL, LLMReasoningEffort
from athome_harness.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage

logger = logging.getLogger(__name__)

_MAX_TRANSPORT_ATTEMPTS = 2
_TRANSPORT_RETRY_DELAY_S = 1.0

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
        reasoning_effort: LLMReasoningEffort = "low",
        timeout_s: float = 30.0,
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
        if timeout_s < 0:
            raise ValueError("timeout_s must not be negative")
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be positive when configured")
        if reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be one of: low, medium, high")
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._timeout_s = timeout_s
        self._session_owned = session is None
        self._session: ChatSession = session or self._build_session()
        self._debug_response_index = 0
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

    def _request_headers(self) -> dict[str, str]:
        """Build the default authentication headers for one completion request."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

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
            # Both fields are required because compatible gateways vary in preference.
            payload["max_tokens"] = self._max_tokens
            payload["max_completion_tokens"] = self._max_tokens
            payload["reasoning_effort"] = self._reasoning_effort
        started = time.monotonic()
        response: ChatResponse | None = None
        for attempt in range(1, _MAX_TRANSPORT_ATTEMPTS + 1):
            try:
                response = self._session.post(
                    self._base_url,
                    json=payload,
                    headers=self._request_headers(),
                    timeout=self._timeout_s,
                )
            except Exception as exc:  # transport-level failure (network, DNS, TLS)
                if attempt < _MAX_TRANSPORT_ATTEMPTS:
                    logger.warning(
                        "[LLM_TRANSPORT_RETRY] provider=<%s> attempt=<%d> reason=<%s>",
                        self.provider_name,
                        attempt,
                        type(exc).__name__,
                    )
                    time.sleep(_TRANSPORT_RETRY_DELAY_S)
                    continue
                logger.warning(
                    "[LLM_CALL] provider=<%s> status=<error> elapsed_s=<%.3f> timeout_s=<%.3f>",
                    self.provider_name,
                    time.monotonic() - started,
                    self._timeout_s,
                )
                logger.error(
                    "[LLM_TRANSPORT_FAILED] provider=<%s> attempts=<%d> reason=<%s>",
                    self.provider_name,
                    attempt,
                    type(exc).__name__,
                )
                raise LLMProviderError(
                    f"{self.provider_name} transport error: {type(exc).__name__}"
                ) from exc
            if response.status_code < 500 or response.status_code >= 600:
                break
            if attempt < _MAX_TRANSPORT_ATTEMPTS:
                logger.warning(
                    "[LLM_TRANSPORT_RETRY] provider=<%s> attempt=<%d> reason=<HTTP_%d>",
                    self.provider_name,
                    attempt,
                    response.status_code,
                )
                time.sleep(_TRANSPORT_RETRY_DELAY_S)

        if response is None:
            raise LLMProviderError(f"{self.provider_name} transport returned no response")
        logger.info(
            "[LLM_CALL] provider=<%s> status=<http_%d> elapsed_s=<%.3f> timeout_s=<%.3f>",
            self.provider_name,
            response.status_code,
            time.monotonic() - started,
            self._timeout_s,
        )
        if not 200 <= response.status_code < 300:
            # API errors often carry a JSON body; surface status only, never body
            # which may embed secrets.
            if response.status_code >= 500:
                logger.error(
                    "[LLM_TRANSPORT_FAILED] provider=<%s> attempts=<%d> reason=<HTTP_%d>",
                    self.provider_name,
                    _MAX_TRANSPORT_ATTEMPTS,
                    response.status_code,
                )
            raise LLMProviderError(f"{self.provider_name} returned HTTP {response.status_code}")
        body = self._parse_body(response)
        usage = self._extract_usage(body)
        self._debug_response_index += 1
        self._debug_dump_response(
            self._debug_response_index,
            body,
            status_code=response.status_code,
            elapsed_seconds=time.monotonic() - started,
        )
        content = self._extract_content(body)
        return content, usage

    def _debug_dump_response(
        self,
        response_index: int,
        body: dict[str, object],
        *,
        status_code: int,
        elapsed_seconds: float,
    ) -> None:
        """Persist a complete provider response envelope when DEBUG is enabled."""
        if os.getenv("DEBUG", "").lower() not in {"1", "true", "yes", "on"}:
            return
        debug_dir = Path("debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": self.provider_name,
            "model": self._model,
            "response_index": response_index,
            "status_code": status_code,
            "elapsed_seconds": elapsed_seconds,
            "body": body,
        }
        (debug_dir / f"llm_provider_response_{response_index:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (debug_dir / "llm_provider_response_last.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _parse_body(self, response: ChatResponse) -> dict[str, object]:
        """Parse the response body into a dict, raising on malformed JSON."""
        try:
            raw = response.json()
        except Exception as exc:
            raise LLMProviderError(
                f"{self.provider_name} returned a response body that could not be parsed as JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise LLMProviderError(
                f"{self.provider_name} returned a response body that is not a JSON object"
            )
        return raw

    def _extract_content(self, body: dict[str, object]) -> str:
        """Extract the assistant message content from the completion body."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProviderError(
                f"{self.provider_name} returned a response missing assistant content"
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMProviderError(
                f"{self.provider_name} returned a response missing assistant content"
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError(
                f"{self.provider_name} returned a response missing assistant content"
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProviderError(
                f"{self.provider_name} returned a response missing assistant content"
            )
        return content

    def _extract_usage(self, body: dict[str, object]) -> LLMUsage:
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
            raise LLMProviderError(
                f"{self.provider_name} returned a response with invalid token usage"
            ) from exc
