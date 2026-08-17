"""Environment and budget configuration for the AtHome harness.

All runtime configuration is loaded from environment variables or a ``.env`` file
via pydantic-settings. The accepted environment keys mirror ``.env.example`` exactly;
any unknown ``ATHOME_``-prefixed environment key raises so the template and the parser
can never drift out of sync (repo invariant).
"""

from __future__ import annotations

import os
from typing import Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Model defaults are verified against OpenRouter and documented in PLAN.md.
DEFAULT_GENERAL_MODEL = "deepseek/deepseek-v4-flash-0731"
DEFAULT_VISION_MODEL = "google/gemma-4-31b-it"

# Environment key prefix that the strict unknown-key guard enforces.
_ATHOME_PREFIX = "ATHOME_"


class Budgets(BaseModel):
    """Resource and politeness limits for a single search session.

    Defaults match SPEC.md section 5 (Budgets and limits). Every field is exposed as
    an ``ATHOME_``-prefixed environment key on :class:`Settings` so operators can
    override any budget without code changes.
    """

    # Rate limiting: requests per interval with a random 0..max jitter added to spread
    # polite spacing. Defaults to 1 request every 2s plus up to 1s jitter.
    rate_requests: int = Field(default=1, ge=0)
    rate_interval_s: float = Field(default=2.0, ge=0)
    rate_jitter_max_s: float = Field(default=1.0, ge=0)

    # VERIFIED: AtHome returns 30 results per page.
    results_per_page: int = Field(default=30, ge=0)

    # DESIGN-FRESH thresholds for the LLM funnel.
    shortlist_size: int = Field(default=20, ge=0)
    recommendations_count: int = Field(default=5, ge=0)

    # Hard ceilings for a single live search.
    max_pages: int = Field(default=100, ge=0)
    runtime_minutes: int = Field(default=30, ge=0)

    # Network and fault tolerance.
    http_timeout_s: float = Field(default=30.0, ge=0)
    proxy_retries: int = Field(default=3, ge=0)

    # Prefetch cache freshness (post-MVP feature gate).
    prefetch_ttl_hours: float = Field(default=48.0, ge=0)

    # Determinism: LLM scoring is always temperature 0 (SPEC section 5).
    llm_temperature: float = Field(default=0.0, ge=0)


class Settings(BaseSettings):
    """Typed view of every runtime environment key.

    Field values are pulled from environment variables (see the ``validation_alias``
    on each field for the exact accepted key) or from ``.env``. The strict
    unknown-key guard in :meth:`_reject_unknown_athome_keys` fails loudly on any
    unexpected ``ATHOME_``-prefixed variable.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = Field(description="OpenRouter API key (required).")
    webshare_proxy_user: str | None = Field(
        default=None, description="Webshare proxy username (optional)."
    )
    webshare_proxy_pass: str | None = Field(
        default=None, description="Webshare proxy password (optional)."
    )

    general_model: str = Field(
        default=DEFAULT_GENERAL_MODEL,
        validation_alias="ATHOME_GENERAL_MODEL",
        description="General-purpose LLM model identifier.",
    )
    vision_model: str = Field(
        default=DEFAULT_VISION_MODEL,
        validation_alias="ATHOME_VISION_MODEL",
        description="Vision-capable LLM model identifier.",
    )

    # Exposed budget knobs (each maps 1:1 to a field on :class:`Budgets`).
    rate_requests: int = Field(
        default=1, validation_alias="ATHOME_RATE_REQUESTS"
    )
    rate_interval_s: float = Field(
        default=2.0, validation_alias="ATHOME_RATE_INTERVAL_S"
    )
    rate_jitter_max_s: float = Field(
        default=1.0, validation_alias="ATHOME_RATE_JITTER_MAX_S"
    )
    results_per_page: int = Field(
        default=30, validation_alias="ATHOME_RESULTS_PER_PAGE"
    )
    shortlist_size: int = Field(
        default=20, validation_alias="ATHOME_SHORTLIST_SIZE"
    )
    recommendations_count: int = Field(
        default=5, validation_alias="ATHOME_RECOMMENDATIONS_COUNT"
    )
    max_pages: int = Field(default=100, validation_alias="ATHOME_MAX_PAGES")
    runtime_minutes: int = Field(
        default=30, validation_alias="ATHOME_RUNTIME_MINUTES"
    )
    http_timeout_s: float = Field(
        default=30.0, validation_alias="ATHOME_HTTP_TIMEOUT_S"
    )
    proxy_retries: int = Field(default=3, validation_alias="ATHOME_PROXY_RETRIES")
    prefetch_ttl_hours: float = Field(
        default=48.0, validation_alias="ATHOME_PREFETCH_TTL_HOURS"
    )
    llm_temperature: float = Field(
        default=0.0, validation_alias="ATHOME_LLM_TEMPERATURE"
    )

    @property
    def budgets(self) -> Budgets:
        """Return the budget knobs as a :class:`Budgets` value object."""
        return Budgets(
            rate_requests=self.rate_requests,
            rate_interval_s=self.rate_interval_s,
            rate_jitter_max_s=self.rate_jitter_max_s,
            results_per_page=self.results_per_page,
            shortlist_size=self.shortlist_size,
            recommendations_count=self.recommendations_count,
            max_pages=self.max_pages,
            runtime_minutes=self.runtime_minutes,
            http_timeout_s=self.http_timeout_s,
            proxy_retries=self.proxy_retries,
            prefetch_ttl_hours=self.prefetch_ttl_hours,
            llm_temperature=self.llm_temperature,
        )

    @model_validator(mode="after")
    def _reject_unknown_athome_keys(self) -> Self:
        """Fail loudly if any ATHOME_-prefixed environment key is not a known field.

        The accepted set is derived from ``model_fields`` (including each field's
        validation alias), so the guard automatically stays in sync with the parser.
        This enforces the invariant that ``.env.example`` and config never drift.
        """
        accepted = {
            str(field.validation_alias if field.validation_alias else field_name).upper()
            for field_name, field in type(self).model_fields.items()
        }
        for raw_key in os.environ:
            normalized = raw_key.upper()
            if normalized.startswith(_ATHOME_PREFIX) and normalized not in accepted:
                raise ValueError(
                    f"Unknown ATHOME_-prefixed environment key '{raw_key}'. "
                    "Add it to Settings and .env.example, or remove it. "
                    f"Accepted keys: {sorted(accepted)}"
                )
        return self
