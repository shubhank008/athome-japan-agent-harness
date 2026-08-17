"""Playwright scraper adapter scaffold (T08).

Placeholder for a future headless-browser backend. It conforms to the
:class:`BaseScraper` contract so switching the harness from the HTTP adapter to
a browser-backed one is a drop-in swap, but every operation currently raises
``NotImplementedError`` because Playwright is a post-MVP dependency decision
(SPEC non-goals) and must not be introduced without deliberation.
"""

from __future__ import annotations

from athome_harness.scraping.base import BaseScraper


class PlaywrightAdapter(BaseScraper):
    """Drop-in ``BaseScraper`` that is not yet implemented.

    Instantiating it is harmless; calling either fetch method raises
    ``NotImplementedError`` with a clear message. The class exists so wiring,
    type annotations, and the adapter-swap seam are already in place.
    """

    _NOT_IMPLEMENTED = (
        "PlaywrightAdapter is a scaffold (T08): browser-backed scraping is not "
        "implemented yet. Use HttpDomAdapter for live sessions."
    )

    def fetch_html(self, url: str) -> str:
        """Raise NotImplementedError with a clear message (playwright TBD)."""
        raise NotImplementedError(self._NOT_IMPLEMENTED)

    def fetch_binary(self, url: str) -> bytes:
        """Raise NotImplementedError with a clear message (playwright TBD)."""
        raise NotImplementedError(self._NOT_IMPLEMENTED)
