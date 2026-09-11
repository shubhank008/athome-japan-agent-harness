"""OpencodeGo LLM transport (milestone M8).

Concrete :class:`BaseLLMProvider` that talks to the OpencodeGo OpenAI-compatible
``chat/completions`` endpoint (``https://opencode.ai/zen/go/v1/chat/completions``,
see the Endpoints section of the OpenCode Go docs). The wire contract is
identical to OpenRouter, so the implementation is a thin declaration over the
shared :class:`OpenAICompatibleProvider` base: it pins the OpencodeGo endpoint,
the ``OPENCODEGO_API_KEY`` environment variable, and the error label.

The provider requires a stable ``x-opencode-session`` header for its lifetime
and an application-specific ``User-Agent`` so requests can be routed and cached
as coding-agent traffic. The current endpoint accepts unprefixed model names,
for example ``deepseek-v4-flash``.
"""

from __future__ import annotations

from uuid import uuid4

from athome_harness.config import DEFAULT_OPENCODEGO_MODEL, DEFAULT_OPENCODEGO_URL
from athome_harness.llm.openai_compat import ChatSession, OpenAICompatibleProvider

# OpencodeGo OpenAI-compatible chat completions endpoint (see /docs/go#endpoints).
# Kept as a module-level constant for importers; the canonical value lives in
# ``config.DEFAULT_OPENCODEGO_URL`` so Settings and the class share one source.
OPENCODEGO_URL = DEFAULT_OPENCODEGO_URL


class OpenCodeGoProvider(OpenAICompatibleProvider):
    """``BaseLLMProvider`` over the OpencodeGo chat completions API."""

    # Error-message label shown in exceptions.
    provider_name = "OpenCodeGo"
    # Default OpenAI-compatible endpoint for OpencodeGo.
    default_base_url = OPENCODEGO_URL
    # Environment variable that carries the OpencodeGo API key.
    env_api_key = "OPENCODEGO_API_KEY"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_OPENCODEGO_MODEL,
        session: ChatSession | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        """Configure an OpencodeGo transport.

        ``api_key`` is required and used only as an ``Authorization`` header.
        ``model`` defaults to the repository OpencodeGo model. ``max_tokens`` is
        the API-level ceiling on completion tokens; when *None* the endpoint
        default is used. ``session`` allows tests to inject a fake transport;
        when omitted a real curl-cffi session is built and owned by this
        instance. ``base_url`` overrides the default OpencodeGo endpoint.
        """
        super().__init__(
            api_key,
            model=model,
            session=session,
            base_url=base_url,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        self._session_id = str(uuid4())

    def _request_headers(self) -> dict[str, str]:
        """Identify this coding-agent conversation to the OpenCodeGo endpoint."""
        headers = super()._request_headers()
        headers.update(
            {
                "User-Agent": "athome-japan-agent-harness/0.1.0",
                "x-opencode-session": self._session_id,
            }
        )
        return headers
