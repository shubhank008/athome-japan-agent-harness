"""Run a headed, operator-driven Playwright challenge observation session."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge

try:
    from playwright_stealth import stealth_async
except ImportError:
    from playwright_stealth import Stealth  # type: ignore[import-untyped]

    async def stealth_async(page: object) -> None:
        """Apply the current playwright-stealth API under the legacy name."""
        await Stealth().apply_stealth_async(page)  # type: ignore[arg-type]

DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"


def _parser() -> argparse.ArgumentParser:
    """Build the manual probe command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--debug-dir", type=Path, default=Path("debug"))
    parser.add_argument("--proxy", default=None, help="Optional Playwright proxy server URL")
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    return parser


def _event(debug_dir: Path, name: str, **fields: object) -> None:
    """Append a redacted event to the probe JSONL log."""
    payload: dict[str, object] = {
        "event": name,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    payload.update(fields)
    with (debug_dir / "playwright_events.jsonl").open("a", encoding="utf-8") as event_file:
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        event_file.write(line + "\n")


async def _capture(page: object, debug_dir: Path, stage: str) -> str:
    """Capture page HTML and screenshot for one manual-observation stage."""
    html = await page.content()  # type: ignore[attr-defined]
    (debug_dir / f"playwright_{stage}.html").write_text(html, encoding="utf-8")
    await page.screenshot(path=str(debug_dir / f"playwright_{stage}.png"))  # type: ignore[attr-defined]
    return html


async def _run(args: argparse.Namespace) -> None:
    """Launch the headed browser and wait for the operator's manual actions."""
    args.debug_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        launch_options: dict[str, object] = {"headless": False}
        if args.proxy:
            launch_options["proxy"] = {"server": args.proxy}
        browser = await playwright.chromium.launch(**launch_options)
        context = await browser.new_context(
            locale="ja-JP",
            record_video_dir=str(args.debug_dir),
            record_video_size={"width": 1280, "height": 720},
        )
        await context.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = await context.new_page()
        try:
            await stealth_async(page)
            await page.goto(args.url, wait_until="domcontentloaded")
            await asyncio.sleep(args.wait_seconds)
            before_html = await _capture(page, args.debug_dir, "before")
            _event(
                args.debug_dir,
                "manual_before",
                challenge_kind=detect_athome_challenge(before_html),
                html_chars=len(before_html),
                body_sha256=hashlib.sha256(before_html.encode()).hexdigest(),
                url=redact_url(page.url),
            )
            print(
                "Browser is ready for manual observation. You may inspect and interact "
                "with the page, then press Enter here to finish."
            )
            await asyncio.to_thread(input)
            after_html = await _capture(page, args.debug_dir, "after")
            _event(
                args.debug_dir,
                "manual_after",
                challenge_kind=detect_athome_challenge(after_html),
                html_chars=len(after_html),
                body_sha256=hashlib.sha256(after_html.encode()).hexdigest(),
                url=redact_url(page.url),
            )
        finally:
            await context.tracing.stop(path=str(args.debug_dir / "playwright_challenge_trace.zip"))
            video = page.video
            await context.close()
            if video is not None:
                generated_video = Path(await video.path())
                target_video = args.debug_dir / "playwright_challenge.webm"
                if generated_video != target_video and generated_video.exists():
                    generated_video.replace(target_video)
            await browser.close()
    print(f"Diagnostics saved under {args.debug_dir}/")


def main() -> None:
    """Parse arguments and run the headed manual probe."""
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
