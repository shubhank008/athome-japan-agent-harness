"""Unit tests for the provider factory (M8).

The factory selects a concrete Interface-Adapter implementation from runtime
:class:`Settings`, so switching providers is a config change. These tests pin
the default wiring (OpenRouter + SQLite + http scraper), the OpencodeGo path,
and the loud failure on an unknown selector without any network side effects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from athome_harness.config import Settings
from athome_harness.llm.opencodego import OpenCodeGoProvider
from athome_harness.llm.openrouter import OpenRouterProvider
from athome_harness.providers import (
    build_llm_provider,
    build_production_fetch,
    build_store,
)
from athome_harness.store.sqlite_store import SqliteStore


def _settings(**overrides: object) -> Settings:
    return Settings(openrouter_api_key="sk-test", **overrides)


def test_llm_defaults_to_openrouter() -> None:
    """Without an override the factory builds the OpenRouter transport."""
    provider = build_llm_provider(_settings())
    assert isinstance(provider, OpenRouterProvider)


def test_llm_switches_to_opencodego(monkeypatch: pytest.MonkeyPatch) -> None:
    """ATHOME_LLM_PROVIDER=opencodego builds the OpencodeGo transport."""
    monkeypatch.setenv("ATHOME_LLM_PROVIDER", "opencodego")
    provider = build_llm_provider(_settings(opencodego_api_key="go-key"))
    assert isinstance(provider, OpenCodeGoProvider)


def test_llm_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized LLM provider identifier fails loudly."""
    monkeypatch.setenv("ATHOME_LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        build_llm_provider(_settings())


def test_store_defaults_to_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an override the factory builds the SQLite store at ATHOME_STORE_PATH."""
    monkeypatch.setenv("ATHOME_STORE_PATH", str(tmp_path / "store.db"))
    store = build_store(_settings())
    try:
        assert isinstance(store, SqliteStore)
    finally:
        store.close()


def test_store_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized store backend fails loudly."""
    monkeypatch.setenv("ATHOME_STORE_PROVIDER", "mongo")
    with pytest.raises(ValueError, match="Unknown store provider"):
        build_store(_settings())


def test_scraper_unknown_provider_raises_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognized scraper provider raises without building live transports."""
    monkeypatch.setenv("ATHOME_SCRAPER_PROVIDER", "nope")
    with pytest.raises(ValueError, match="Unknown scraper provider"):
        build_production_fetch(settings=_settings())
