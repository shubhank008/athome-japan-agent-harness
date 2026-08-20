"""Shared, testable helpers for the operator probe scripts.

The probe scripts (``scripts/*_probe.py``) walk real network adapters and are
bounded, opt-in diagnostics for a human operator. This module holds the pure,
side-effect-free functions they all need so the behavior can be unit-tested
without touching the network or the filesystem:

* :func:`validate_page_content` fails closed on an AtHome challenge or a page
  with no usable listing content, matching the repository invariant that a
  challenge page is never parsed or saved as data.
* :func:`safe_artifact_path` returns an artifact path under a probe debug
  directory, silently refusing to escape it.
* :func:`redact_diagnostics` scrubs credentials and query strings from URLs
  used in report output.

No secrets, cookies, session state, proxy URLs, or challenge HTML is ever
written by these helpers. Challenge markers intentionally reuse the shared
detector in :mod:`athome_harness.scraping.challenge` so probes cannot drift
from the production safety boundary.
"""

from __future__ import annotations

import logging
from pathlib import Path

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge

logger = logging.getLogger(__name__)


class ProbeContentError(RuntimeError):
    """Raised when a page is a challenge, a block, or carries no listing content.

    The message is operator-safe: it names the stage and the redacted source but
    never the full URL, query string, proxy, or credentials.
    """

    def __init__(self, stage: str, source: str, detail: str) -> None:
        self.stage = stage
        self.source = source
        super().__init__(f"[{stage}] {detail} (source=<{source}>)")
        logger.error("%s", self)


def validate_page_content(html: str, *, stage: str, source: str) -> None:
    """Validate that ``html`` is real page content, failing closed otherwise.

    Raises :class:`ProbeContentError` when ``html`` is an AtHome challenge page
    (the production safety boundary) or when it has no obviously usable content.
    Operators never parse or save a challenge page as data, matching the
    invariant in ``AGENTS.md``.

    ``stage`` names the probe step (for example ``fetch`` or ``detail_parse``)
    and ``source`` is the redacted URL or fixture name that produced ``html``.
    """
    challenge = detect_athome_challenge(html)
    if challenge is not None:
        raise ProbeContentError(
            stage,
            source,
            f"at-home challenge detected (kind=<{challenge}>); refusing to parse or save",
        )
    stripped = html.strip()
    if not stripped:
        raise ProbeContentError(stage, source, "empty page content; nothing to validate")
    if len(stripped) < 64:
        raise ProbeContentError(stage, source, "page content too short to be listing data")


def safe_artifact_path(debug_dir: Path, name: str) -> Path:
    """Return an artifact path under ``debug_dir`` for ``name``.

    Refuses to traverse above ``debug_dir`` (for example a ``name`` containing
    ``..``) so probe output cannot escape its sandbox. The directory is created
    on demand.
    """
    debug_dir.mkdir(parents=True, exist_ok=True)
    candidate = (debug_dir / name).resolve()
    root = debug_dir.resolve()
    if not candidate.is_relative_to(root):
        raise ProbeContentError("artifact", str(debug_dir), f"unsafe artifact name <{name}>")
    return candidate


def redact_diagnostics(raw: str) -> str:
    """Redact credentials and query strings from a free-text diagnostic string.

    Applies :func:`athome_harness.scraping.base.redact_url` to every URL-looking
    token so operator reports never expose proxy credentials or private query
    parameters. Non-URL tokens pass through unchanged.
    """
    import re

    url_pattern = re.compile(r"https?://[^\s\"'<>]+")
    return url_pattern.sub(lambda m: redact_url(m.group(0)), raw)


__all__ = [
    "ProbeContentError",
    "redact_diagnostics",
    "safe_artifact_path",
    "validate_page_content",
]
