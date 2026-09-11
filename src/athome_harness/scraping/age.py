"""Helpers for representing observed building age consistently."""

from __future__ import annotations

from datetime import date


def age_values(
    raw: str | None, ref_date: date, build_date: date | None
) -> tuple[float | None, str | None, str | None]:
    """Return rounded years, raw input, and a human-readable age display."""
    if not raw:
        return None, None, None
    if build_date is None:
        return None, raw, None
    months = max(0, (ref_date.year - build_date.year) * 12 + ref_date.month - build_date.month)
    years = round(months / 12.0, 1)
    if months < 12:
        count = max(1, months)
        display = f"{count} month{'s' if count != 1 else ''} old"
    else:
        display = f"{years:g} year{'s' if years != 1 else ''} old"
    return years, raw, display
