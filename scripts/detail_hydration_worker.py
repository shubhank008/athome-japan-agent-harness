"""Run the disabled-by-default detail hydration worker."""

from __future__ import annotations

import argparse
import sys

from athome_harness.providers import build_production_fetch, build_store, load_settings
from athome_harness.scraping.detail_hydration_worker import DetailHydrationWorker
from athome_harness.scraping.rate_limiter import TokenBucketRateLimiter


def main() -> int:
    """Run one bounded invocation or an explicitly requested loop."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.hydration_worker_enabled:
        print("detail hydration worker disabled; set ATHOME_HYDRATION_WORKER_ENABLED=true")
        return 0
    store = build_store(settings)
    fetch = build_production_fetch(settings=settings)
    try:
        worker = DetailHydrationWorker(
            store,
            fetch,
            limiter=TokenBucketRateLimiter(settings.budgets),
            max_jobs=settings.hydration_worker_max_jobs,
            idle_sleep_seconds=settings.hydration_worker_sleep_s,
        )
        worker.run_loop() if args.loop else worker.run_once()
    finally:
        close_fetch = getattr(fetch, "close", None)
        if callable(close_fetch):
            close_fetch()
        close_store = getattr(store, "close", None)
        if callable(close_store):
            close_store()
    return 0


if __name__ == "__main__":
    sys.exit(main())
