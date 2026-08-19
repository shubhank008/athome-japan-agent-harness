"""Tests for the shared curl-cffi session-state module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from athome_harness.scraping.cookie_handoff import CookieHandoff
from athome_harness.scraping.session_state import (
    SessionState,
    build_chrome_headers,
    chrome_major,
    get_installed_chrome_version,
)


def test_chrome_major_extracts_leading_segment() -> None:
    """The major version is the leading dot-delimited segment."""
    assert chrome_major("126.0.6478.127") == "126"
    assert chrome_major("151.0.0.0") == "151"


def test_build_chrome_headers_echo_major_version() -> None:
    """The sec-ch-ua envelope reflects the requested Chrome major version."""
    headers = build_chrome_headers("126.0.6478.127")
    assert '"Google Chrome";v="126"' in headers["sec-ch-ua"]
    assert headers["user-agent"].startswith("Mozilla/5.0 (X11; Linux x86_64)")
    assert "Chrome/126.0.6478.127" in headers["user-agent"]
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["accept-language"] == "ja,en-US;q=0.9,en;q=0.8"


def test_get_installed_chrome_version_returns_string() -> None:
    """The installed version probe always yields a dotted version string."""
    assert isinstance(get_installed_chrome_version(), str)
    assert len(get_installed_chrome_version().split(".")) == 4


def test_session_state_round_trips_through_disk(tmp_path: Path) -> None:
    """A saved session state can be reloaded with identical fields."""
    state = SessionState.from_browser(
        cookies=[
            {"name": "reese84", "value": "clearance", "domain": ".athome.co.jp"},
            {"name": "non-string", "value": 42, "domain": ".athome.co.jp"},
        ],
        user_agent="Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36",
        chrome_version="126.0.6478.127",
        proxy_url="http://proxy.example:8080",
    )
    path = tmp_path / "nested" / "session_state.json"
    state.save(path)
    assert path.exists()

    reloaded = SessionState.load(path)
    assert reloaded.cookies == {"reese84": "clearance"}
    assert "non-string" not in reloaded.cookies
    assert reloaded.user_agent == state.user_agent
    assert reloaded.proxy_url == "http://proxy.example:8080"
    assert reloaded.chrome_major_version == "126"
    assert reloaded.headers == state.headers


def test_session_state_to_and_from_cookie_handoff(
    tmp_path: Path,
) -> None:
    """A session state converts losslessly to and from the typed handoff."""
    state = SessionState.from_browser(
        cookies=[{"name": "reese84", "value": "clearance"}],
        user_agent="Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36",
        chrome_version="131.0.6778.0",
    )
    handoff = state.to_cookie_handoff()
    assert isinstance(handoff, CookieHandoff)
    assert handoff.cookie_values == {"reese84": "clearance"}
    assert "Chrome/131.0.0.0" in handoff.user_agent

    rebuilt = SessionState.from_cookie_handoff(handoff)
    assert rebuilt.cookies == {"reese84": "clearance"}
    assert rebuilt.chrome_major_version == "131"
    assert rebuilt.user_agent == handoff.user_agent


def test_session_state_load_rejects_malformed_file(tmp_path: Path) -> None:
    """A missing or non-object session state is rejected, not silently parsed."""
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError):
        SessionState.load(missing)

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError):
        SessionState.load(bad)
