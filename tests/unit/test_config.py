"""Unit tests for environment configuration and budget defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from athome_harness.config import Budgets, Settings

_ISOLATED_KEYS = ("OPENROUTER_API_KEY",)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin OPENROUTER_API_KEY and drop stray ATHOME_ variables so tests are isolated."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for key in list(__import__("os").environ):
        if key.startswith("ATHOME_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def settings(clean_env: None) -> Settings:
    """A Settings instance with no local .env file to avoid ambient interference."""
    return Settings()


def test_defaults_load(settings: Settings) -> None:
    """Without overrides, every knob falls back to the SPEC.md section 5 default."""
    assert settings.general_model == "deepseek/deepseek-v4-flash-0731"
    assert settings.vision_model == "google/gemma-4-31b-it"
    assert settings.budgets == Budgets()


def test_budgets_defaults(settings: Settings) -> None:
    """Budgets expose the exact defaults from the spec budget table."""
    b = settings.budgets
    assert (b.rate_requests, b.rate_interval_s, b.rate_jitter_max_s) == (1, 2.0, 1.0)
    assert b.results_per_page == 30
    assert b.shortlist_size == 20
    assert b.recommendations_count == 5
    assert b.max_pages == 100
    assert b.runtime_minutes == 30
    assert b.http_timeout_s == 30.0
    assert b.proxy_retries == 3
    assert b.prefetch_ttl_hours == 48.0
    assert b.llm_temperature == 0.0


def test_env_overrides_settings(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit env keys override the defaults."""
    monkeypatch.setenv("ATHOME_GENERAL_MODEL", "custom/model")
    monkeypatch.setenv("ATHOME_MAX_PAGES", "50")
    monkeypatch.setenv("ATHOME_LLM_TEMPERATURE", "0.3")
    s = Settings()
    assert s.general_model == "custom/model"
    assert s.max_pages == 50
    assert s.llm_temperature == 0.3


def test_unknown_athome_env_key_raises(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """An undocumented ATHOME_ key must fail loudly to keep parser and .env in sync."""
    monkeypatch.setenv("ATHOME_NOPE_NOT_A_KEY", "1")
    with pytest.raises(ValueError, match="ATHOME_NOPE_NOT_A_KEY"):
        Settings()


@pytest.mark.parametrize(
    "key",
    [
        "ATHOME_GENERAL_MODEL",
        "ATHOME_VISION_MODEL",
        "ATHOME_RATE_REQUESTS",
        "ATHOME_RATE_INTERVAL_S",
        "ATHOME_RATE_JITTER_MAX_S",
        "ATHOME_RESULTS_PER_PAGE",
        "ATHOME_SHORTLIST_SIZE",
        "ATHOME_RECOMMENDATIONS_COUNT",
        "ATHOME_MAX_PAGES",
        "ATHOME_RUNTIME_MINUTES",
        "ATHOME_HTTP_TIMEOUT_S",
        "ATHOME_PROXY_RETRIES",
        "ATHOME_PREFETCH_TTL_HOURS",
        "ATHOME_LLM_TEMPERATURE",
    ],
)
def test_every_env_example_athome_key_is_accepted(
    clean_env: None, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ATHOME_ key documented in .env.example is understood by the parser.

    This keeps the template and the parser in sync in both directions.
    """
    monkeypatch.setenv(key, "1")
    Settings()


def test_env_example_lists_only_documented_athome_keys() -> None:
    """The set of ATHOME_ keys in .env.example exactly matches the parser's set."""
    repo_root = Path(__file__).resolve().parents[2]
    example = (repo_root / ".env.example").read_text(encoding="utf-8")
    example_keys = {
        line.split("=")[0] for line in example.splitlines() if line.startswith("ATHOME_")
    }
    parser_keys = {
        str(field.validation_alias if field.validation_alias else name).upper()
        for name, field in Settings.model_fields.items()
        if field.validation_alias
        and str(field.validation_alias).upper().startswith("ATHOME_")
    }
    assert example_keys == parser_keys
