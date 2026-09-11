"""LLM shortlister (milestone M4, T20).

Scores a harvest of :class:`ListingSummary` values against the query's soft
preferences in token-bounded batches and returns the ordered top X with a
one-line rationale per pick. This is the ``harvest -> top X`` step of the
funnel (US-003).

Batching is deterministic and token-aware:

* Each listing is serialized to a compact JSON line and its token cost is
  estimated (``chars_per_token`` heuristic, default 4).
* Listings are packed into batches that never exceed ``max_batch_tokens``, so
  prompt size is bounded regardless of harvest size.
* Each batch is scored independently (temperature 0) and the merged results are
  sorted by score descending before the top X is returned.

Scoring is fully driven by an injected :class:`BaseLLMProvider`, so this module
performs no network I/O and is testable with a canned provider, including a
determinism check (same input yields the same output) and a budget check (a
batch never exceeds the configured token ceiling).
"""

from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from athome_harness.llm.base import BaseLLMProvider
from athome_harness.models import ListingSummary

logger = logging.getLogger(__name__)

# Rough token estimate: this many characters count as one token.
# Used only to budget batch prompts, not for exact accounting.
DEFAULT_CHARS_PER_TOKEN = 4

# Default ceiling for one scoring batch prompt, in estimated tokens.
DEFAULT_MAX_BATCH_TOKENS = 6000
DEFAULT_MAX_WORKERS = 2
DEFAULT_TOP_X = 20

_SYSTEM_PROMPT = (
    "You rank rental and purchase listings against a list of soft preferences "
    "the user expressed. Score each listing from 0 (poor fit) to 10 (excellent "
    "fit) and give a one-line rationale. Be consistent: identical listing data "
    "and preferences must always produce the same score. Return JSON with a "
    "'entries' array where each entry has listing_id, score and rationale."
)


class ShortlistEntry(BaseModel):
    """One scored listing from the shortlister.

    ``listing_id`` references the source :class:`ListingSummary.internal_id`;
    ``score`` is a 0-10 fit score; ``rationale`` is a short human reason.
    """

    listing_id: str = Field(description="Internal ID of the scored listing.")
    score: float = Field(ge=0, le=10, description="Fit score from 0 to 10.")
    rationale: str = Field(description="One-line rationale for the score.")


class ShortlistBatch(BaseModel):
    """Model output for one scoring batch."""

    entries: list[ShortlistEntry] = Field(
        default_factory=list, description="Scored entries for the batch."
    )


class Shortlister:
    """Batches and scores listings against soft preferences.

    ``provider`` performs the scoring; ``chars_per_token`` and
    ``max_batch_tokens`` control how listings are packed into prompt batches.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
        max_batch_tokens: int = DEFAULT_MAX_BATCH_TOKENS,
        max_workers: int = DEFAULT_MAX_WORKERS,
        example_path: Path = Path("debug/shortlist_example.json"),
    ) -> None:
        self._provider = provider
        self._chars_per_token = max(1, chars_per_token)
        self._max_batch_tokens = max(1, max_batch_tokens)
        self._max_workers = max(1, max_workers)
        self._example_path = example_path

    def shortlist(
        self,
        prefs: list[str],
        listings: list[ListingSummary],
        *,
        top_x: int | None = None,
        temperature: float = 0.0,
    ) -> list[ShortlistEntry]:
        """Return the ordered top-``top_x`` scored listings.

        Listings are packed into token-bounded batches, scored, merged, sorted
        by score descending, and truncated to ``top_x``. The default is the
        configured top-X value, not the full harvest.
        """
        if not listings:
            return []
        top = DEFAULT_TOP_X if top_x is None else max(0, top_x)
        if top == 0:
            return []
        source_ids = {listing.internal_id for listing in listings}
        batches = self._pack_batches(prefs, listings)
        self._write_example(batches[0])
        logger.info(
            "[SHORTLIST_START] candidates=%d top_x=%d max_batch_tokens=%d "
            "estimated_batches=%d max_workers=%d",
            len(listings),
            top,
            self._max_batch_tokens,
            len(batches),
            self._max_workers,
        )
        best_by_id: dict[str, ShortlistEntry] = {}
        successes = failures = 0
        prompt_tokens = completion_tokens = 0
        shortlist_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures: dict[
                Future[tuple[list[ShortlistEntry], int, int]],
                tuple[int, list[ListingSummary], int, float],
            ] = {}
            for index, batch in enumerate(batches, start=1):
                estimated = self._estimate_batch_tokens(prefs, batch)
                logger.debug(
                    "[SHORTLIST_BATCH_START] batch=%d/%d actual_size=%d estimated_prompt_tokens=%d",
                    index,
                    len(batches),
                    len(batch),
                    estimated,
                )
                submitted = time.monotonic()
                future = executor.submit(self._score_batch, prefs, batch, temperature)
                futures[future] = (index, batch, estimated, submitted)
            for future in as_completed(futures):
                index, batch, estimated, submitted = futures[future]
                try:
                    entries, batch_prompt_tokens, batch_completion_tokens = future.result()
                except Exception as exc:  # noqa: BLE001 - one batch must not lose others
                    failures += 1
                    logger.warning(
                        "[SHORTLIST_BATCH_FAILED] batch=%d/%d actual_size=%d "
                        "estimated_prompt_tokens=%d elapsed_s=<%.3f> error=<%s>",
                        index,
                        len(batches),
                        len(batch),
                        estimated,
                        time.monotonic() - submitted,
                        type(exc).__name__,
                    )
                    continue
                successes += 1
                prompt_tokens += batch_prompt_tokens
                completion_tokens += batch_completion_tokens
                logger.info(
                    "[SHORTLIST_BATCH_DONE] batch=%d/%d actual_size=%d "
                    "estimated_prompt_tokens=%d prompt_tokens=%d completion_tokens=%d "
                    "elapsed_s=<%.3f>",
                    index,
                    len(batches),
                    len(batch),
                    estimated,
                    batch_prompt_tokens,
                    batch_completion_tokens,
                    time.monotonic() - submitted,
                )
                for entry in entries:
                    if entry.listing_id not in source_ids:
                        logger.warning(
                            "shortlister referenced unknown listing %s", entry.listing_id
                        )
                        continue
                    previous = best_by_id.get(entry.listing_id)
                    if previous is None or entry.score > previous.score:
                        best_by_id[entry.listing_id] = entry
        logger.info(
            "[SHORTLIST_DONE] shortlisted=%d successful_batches=%d failed_batches=%d "
            "prompt_tokens=%d completion_tokens=%d elapsed_s=<%.3f>",
            min(top, len(best_by_id)),
            successes,
            failures,
            prompt_tokens,
            completion_tokens,
            time.monotonic() - shortlist_started,
        )
        merged = sorted(best_by_id.values(), key=lambda e: (-e.score, e.listing_id))
        return merged[:top]

    def _estimate_batch_tokens(self, prefs: list[str], batch: list[ListingSummary]) -> int:
        """Estimate the complete prompt token count for one scoring batch."""
        fixed = self._estimate_tokens(json.dumps(prefs, ensure_ascii=False))
        return fixed + sum(self._estimate_tokens(self._serialize(item)) for item in batch)

    def _write_example(self, batch: list[ListingSummary]) -> None:
        """Write one local example for operator payload review."""
        if self._example_path.exists():
            return
        self._example_path.parent.mkdir(parents=True, exist_ok=True)
        self._example_path.write_text(
            json.dumps(batch[0].model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _pack_batches(
        self, prefs: list[str], listings: list[ListingSummary]
    ) -> list[list[ListingSummary]]:
        """Pack listings into token-bounded batches.

        Includes the preferences serialized once per batch plus each listing
        line; a batch is flushed whenever adding another listing would exceed
        ``max_batch_tokens``.
        """
        fixed_cost = self._estimate_tokens(json.dumps(prefs, ensure_ascii=False))
        batches: list[list[ListingSummary]] = []
        current: list[ListingSummary] = []
        current_cost = fixed_cost
        for listing in listings:
            line = self._serialize(listing)
            listing_cost = self._estimate_tokens(line)
            # A single oversized listing still gets its own batch so nothing is
            # ever skipped (the batch just carries one heavy item).
            if current and current_cost + listing_cost > self._max_batch_tokens:
                batches.append(current)
                current = []
                current_cost = fixed_cost
            current.append(listing)
            current_cost += listing_cost
        if current:
            batches.append(current)
        return batches

    def _score_batch(
        self, prefs: list[str], batch: list[ListingSummary], temperature: float
    ) -> tuple[list[ShortlistEntry], int, int]:
        """Score one batch against the preferences and return its entries."""
        serialized = "\n".join(self._serialize(item) for item in batch)
        user = (
            f"Preferences: {prefs}\n\nListings:\n{serialized}\n\n"
            "Return JSON with an 'entries' array ordered by how well each "
            "listing fits the preferences."
        )
        output, usage = self._provider.complete_json(
            system=_SYSTEM_PROMPT,
            user=user,
            schema=ShortlistBatch,
            temperature=temperature,
        )
        logger.debug(
            "[SHORTLIST_BATCH] items=%d prompt_tokens=%d completion_tokens=%d",
            len(batch),
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        return output.entries, usage.prompt_tokens, usage.completion_tokens

    def _estimate_tokens(self, text: str) -> int:
        """Estimate the token cost of ``text`` for budget packing."""
        return max(1, math.ceil(len(text) / self._chars_per_token))

    @staticmethod
    def _serialize(listing: ListingSummary) -> str:
        """Serialize a listing to one compact JSON line for scoring."""
        return json.dumps(
            listing.model_dump(),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
