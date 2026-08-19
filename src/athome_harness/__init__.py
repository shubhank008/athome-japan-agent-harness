"""AtHome Japan Home Finder agent harness.

A conversational CLI agent that turns natural-language housing wishes into ranked
rental and purchase recommendations from athome.co.jp.

This package is the single entry point for the harness. Submodules are added per
milestone and mirror the architecture tree in
``docs/specs/001-athome-home-finder/plan.md``.

The :class:`~athome_harness.cli.SearchSession` orchestrator and
:func:`~athome_harness.cli.parse_command` REPL parser are the public M6 entry
points for driving a search session programmatically or interactively.
"""

__version__ = "0.1.0"

from athome_harness.cli import (
    Command,
    SearchOutcome,
    SearchSession,
    SessionDeps,
    parse_command,
)

__all__ = [
    "Command",
    "SearchOutcome",
    "SearchSession",
    "SessionDeps",
    "parse_command",
]
