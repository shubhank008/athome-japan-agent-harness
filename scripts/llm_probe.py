"""Bounded operator probe for the configured LLM provider.

Drives the real provider path an operator would exercise to confirm the LLM
layer is wired correctly: select the configured provider through the factory
(:func:`athome_harness.providers.build_llm_provider`), send a prompt, and parse
the response through the same schema-validated ``complete_json`` code path the
query parser, shortlister, and recommender use. It reports the resolved
provider/model and the token usage, and prints only the parsed output, never
any credential.

Authentication is handled by the provider factory and the concrete transports:
when the requested provider's API key is absent, construction fails loudly
(:class:`LLMProviderError`) instead of silently using a real secret. The
``--fake`` mode bypasses the network entirely with a canned provider so the
schema-validated path can be verified deterministically offline.

RUN (live):     PYTHONPATH=src python scripts/llm_probe.py --prompt "2 bedrooms in Osaka"
RUN (offline):  PYTHONPATH=src python scripts/llm_probe.py --fake --prompt "2 bedrooms in Osaka"
HELP (no net):  PYTHONPATH=src python scripts/llm_probe.py --help
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _root in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from pydantic import BaseModel, Field  # noqa: E402

from athome_harness.config import Settings  # noqa: E402
from athome_harness.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage  # noqa: E402
from athome_harness.providers import load_settings  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = "2 bedrooms near a station in Osaka, rent under 80k"


class ProbeResponse(BaseModel):
    """Small schema-validated output the probe asks the provider to produce.

    Exercising ``complete_json`` against a real pydantic schema exercises the
    same parse-validate-repair loop the production funnel uses.
    """

    flow: str = Field(description="Rental or purchase flow inferred from the prompt.")
    prefecture: str = Field(description="Target prefecture inferred from the prompt.")
    summary: str = Field(description="One-line summary of the inferred intent.")


class CannedProvider(BaseLLMProvider):
    """Deterministic no-network provider for the ``--fake`` verification path.

    Reflects the prompt topic back into a valid :class:`ProbeResponse` so the
    probe can confirm the full schema-validated path without any credential or
    live request.
    """

    def __init__(self, *, model: str = "fake/canned") -> None:
        self._model = model
        self._usage = LLMUsage(prompt_tokens=7, completion_tokens=3)

    @property
    def model(self) -> str:
        """Return the (fake) model identifier for this probe provider."""
        return self._model

    def complete_text(
        self, *, system: str, user: str, temperature: float = 0.0
    ) -> tuple[str, LLMUsage]:
        flow = "rent" if "buy" not in user.lower() else "buy"
        import json

        body: dict[str, object] = {
            "flow": flow,
            "prefecture": "osaka",
            "summary": f"canned parse of {user[:40]}",
        }
        return json.dumps(body), self._usage


def _parser() -> argparse.ArgumentParser:
    """Build the LLM probe command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM provider identifier (openrouter|opencodego). Defaults to configured.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model identifier. Defaults to the configured model for the provider.",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send.")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use a canned no-network provider instead of the configured transport.",
    )
    return parser


def _build_provider(args: argparse.Namespace) -> BaseLLMProvider:
    """Build the probe provider, failing loudly when credentials are absent."""
    if args.fake:
        model = args.model or "fake/canned"
        return CannedProvider(model=model)
    settings = load_settings()
    if args.provider is not None:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    provider = _factory_build(settings, args.model)
    return provider


def _factory_build(settings: Settings, model: str | None) -> BaseLLMProvider:
    """Invoke the real provider factory with optional model override."""
    from athome_harness.providers import build_llm_provider

    if settings.llm_provider.strip().lower() == "opencodego" and model is not None:
        settings = settings.model_copy(update={"opencodego_model": model})
    if settings.llm_provider.strip().lower() == "openrouter" and model is not None:
        settings = settings.model_copy(update={"general_model": model})
    return build_llm_provider(settings)


def _model_label(provider: BaseLLMProvider, args: argparse.Namespace) -> str:
    """Return a safe provider/model label, defaulting to the fake model."""
    model = getattr(provider, "model", None)
    if model:
        return str(model)
    return args.model or "configured"


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, build the provider, and run one schema-validated call."""
    logging.basicConfig(level=logging.INFO)
    args = _parser().parse_args(argv)
    system = (
        "You infer a rental flow, prefecture, and a one-line summary from a "
        "housing wish. Return JSON matching the ProbeResponse schema."
    )
    try:
        provider = _build_provider(args)
        label = _model_label(provider, args)
        response, usage = provider.complete_json(
            system=system,
            user=args.prompt,
            schema=ProbeResponse,
            temperature=0.0,
        )
        print(f"provider: {_resolved_provider_label(provider, args.fake)}")
        print(f"model: {label}")
        print(f"flow: {response.flow}")
        print(f"prefecture: {response.prefecture}")
        print(f"summary: {response.summary}")
        print(
            f"tokens: prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
            f"total={usage.total}"
        )
        return 0
    except LLMProviderError as exc:
        logger.error("[LLM_PROBE_FAILED] %s", exc)
        print(f"provider error: {exc}")
        return 3
    except ValueError as exc:
        logger.error("[LLM_PROBE_CONFIG] %s", exc)
        print(f"configuration error: {exc}")
        return 2
    except (KeyboardInterrupt, SystemExit):
        return 130


def _resolved_provider_label(provider: BaseLLMProvider, fake: bool) -> str:
    """Return a human identifier for the provider (fake or transport class)."""
    if fake:
        return "fake"
    return type(provider).__name__.removesuffix("Provider").lower()


if __name__ == "__main__":
    sys.exit(main())
