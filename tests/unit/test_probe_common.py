"""Unit tests for the shared operator-probe helpers in ``scripts.probe_common``.

These exercise the real, side-effect-free helper functions used by every probe
script so their safety boundaries are locked down: content validation fails
closed on an AtHome challenge, artifact paths cannot escape their sandbox, and
diagnostics redact credentials and query strings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.probe_common import (
    ProbeContentError,
    redact_diagnostics,
    safe_artifact_path,
    validate_page_content,
)

_CHALLENGE_PAGE = (
    "<html><body><h1>Click to verify</h1>"
    "<p>To regain access, please make sure that cookies and JavaScript are "
    "enabled</p></body></html>"
)


def test_validate_rejects_athome_puzzle_challenge() -> None:
    """A challenge page must never pass content validation (puzzle kind)."""
    with pytest.raises(ProbeContentError) as excinfo:
        validate_page_content(_CHALLENGE_PAGE, stage="list_fetch", source="<redacted>")
    assert "list_fetch" in str(excinfo.value)
    assert "challenge" in str(excinfo.value)


def test_validate_rejects_empty_content() -> None:
    """Empty content fails closed rather than being treated as usable data."""
    with pytest.raises(ProbeContentError):
        validate_page_content("   ", stage="detail_parse", source="fixture.html")


def test_validate_accepts_real_listing_html() -> None:
    """Real page content passes validation without raising."""
    html = "<html><body><div class='p-property--building'>unit</div></body></html>"
    validate_page_content(html, stage="list_parse", source="fixture.html")


def test_safe_artifact_path_refuses_traversal() -> None:
    """An artifact name with ``..`` must not escape the debug directory."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        debug_dir = Path(tmp) / "debug"
        with pytest.raises(ProbeContentError):
            safe_artifact_path(debug_dir, "../escape.txt")


def test_safe_artifact_path_returns_nested_path() -> None:
    """A safe artifact name resolves inside the debug directory and is created."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        debug_dir = Path(tmp) / "debug"
        path = safe_artifact_path(debug_dir, "artifact.txt")
        assert path.parent == debug_dir.resolve()
        assert path.name == "artifact.txt"


def test_redact_diagnostics_strips_credentials_and_query() -> None:
    """Operators must never see proxy credentials or private query strings."""
    raw = "proxied via http://user:secret@proxy.example:8080/path?token=abc123"
    out = redact_diagnostics(raw)
    assert "secret" not in out
    assert "token=abc123" not in out
    assert out.startswith("proxied via http://proxy.example:8080/path")
