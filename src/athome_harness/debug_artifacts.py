"""Persistent DEBUG artifact directory helpers."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_DEBUG_ROOT = Path("debug")
_DEFAULT_REPORT_ROOT = Path("reports")


def debug_run_dir(*, root: Path = _DEFAULT_DEBUG_ROOT, run_id: str | None = None) -> Path:
    """Return a stable per-run DEBUG directory, creating it when DEBUG is enabled."""
    if os.getenv("DEBUG", "").lower() not in {"1", "true", "yes", "on"}:
        return root
    resolved_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / resolved_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_run_dir(*, root: Path = _DEFAULT_REPORT_ROOT, run_id: str) -> Path:
    """Return and create the repository-local report directory for one run."""
    path = root / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
