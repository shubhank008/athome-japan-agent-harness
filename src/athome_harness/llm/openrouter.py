"""OpenRouter LLM transport (milestone M4, T18).

Concrete :class:`BaseLLMProvider` that talks to the OpenRouter chat
completions endpoint. The shared wire contract (request payload, JSON parsing,
token accounting, safe error handling) lives in
:mod:`athome_harness.llm.openai_compat`; this module is a thin declaration that
pins OpenRouter's endpoint URL, API-key environment variable, and error label.
Per the Abstract First invariant, curl-cffi is imported only in the shared
base, never here.
"""

from __future__ import annotations

from athome_harness.llm.openai_compat import (
    OpenAICompatibleProvider,
)

# OpenRouter chat completions endpoint.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(OpenAICompatibleProvider):
    """``BaseLLMProvider`` over the OpenRouter chat completions API."""

    # Error-message label shown in exceptions.
    provider_name = "OpenRouter"
    # Default OpenAI-compatible endpoint for OpenRouter.
    default_base_url = OPENROUTER_URL
    # Environment variable that carries the OpenRouter API key.
    env_api_key = "OPENROUTER_API_KEY"
