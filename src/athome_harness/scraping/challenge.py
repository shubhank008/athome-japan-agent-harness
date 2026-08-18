"""AtHome challenge markers shared by concrete scraper adapters."""

from __future__ import annotations

# HTTP 200 AtHome puzzle/authentication markers. These exact strings are part of
# the existing marker contract and are intentionally conservative.
_ATHOME_PUZZLE_MARKERS = (
    "click to verify",
    "認証にご協力ください",
)
_ATHOME_JAVASCRIPT_MARKERS = (
    "to regain access, please make sure that cookies and javascript are enabled",
)


def detect_athome_challenge(body: str) -> str | None:
    """Return the challenge kind in ``body``, or ``None`` for page content."""
    lowered = body.lower()
    if any(marker in lowered for marker in _ATHOME_PUZZLE_MARKERS):
        return "puzzle"
    if any(marker in lowered for marker in _ATHOME_JAVASCRIPT_MARKERS):
        return "javascript"
    return None
