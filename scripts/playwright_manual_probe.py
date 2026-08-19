"""
Run a headed, operator-driven Patchright challenge observation session.
Headless: PYTHONPATH=src python scripts/playwright_manual_probe.py
Headed: PYTHONPATH=src xvfb-run python scripts/playwright_manual_probe.py

"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from patchright.async_api import Page, async_playwright

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge
from athome_harness.scraping.playwright_shared import (
    CHALLENGE_SELECTOR,
    LISTING_SELECTOR,
    intercept_route,
    read_settled_content,
    set_route_logger,
    solve_geetest_capsolver,
    solve_turnstile_capsolver,
    wait_for_page_signal,
)
from athome_harness.scraping.session_state import (
    SessionState,
    build_launch_options,
    get_installed_chrome_version,
)

try:
    from playwright_stealth import stealth_async
except ImportError:
    from playwright_stealth import Stealth  # type: ignore[import-untyped]

    async def stealth_async(page: object) -> None:
        """Apply the current playwright-stealth API under the legacy name."""
        await Stealth().apply_stealth_async(page)


DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"
# Set to true to capture and save screenshot, video and tracestack
DEBUG = False


def _parser() -> argparse.ArgumentParser:
    """Build the manual probe command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--debug-dir", type=Path, default=Path("debug"))
    parser.add_argument("--proxy", default=None, help="Optional Patchright proxy server URL")
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    parser.add_argument(
        "--solve-mode",
        choices=["none", "click", "capsolver"],
        default="click",
        help=(
            "Challenge handling strategy: 'none' (observe only), 'click' "
            "(auto-interact), 'capsolver' (API solver)"
        ),
    )
    parser.add_argument("--capsolver-key", default=os.getenv("CAPSOLVER_API_KEY", None))
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
    if DEBUG:
        # Screenshot first so we know what the browser is seeing
        screenshot_path = str(debug_dir / f"playwright_{stage}.png")
        await page.screenshot(path=screenshot_path)  # type: ignore[attr-defined]
    html = await read_settled_content(cast(Page, page))
    (debug_dir / f"playwright_{stage}.html").write_text(html, encoding="utf-8")
    return cast(str, html)


def _getTime() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clearDebug(folder_path: str) -> None:
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    print(_getTime() + " - Cleared existing debug data")


async def _human_move_and_click(page: Any, x: float, y: float) -> None:
    """Move the mouse to coordinates with a randomized curve and click."""
    # Start from current position or a random top corner
    start_x = random.randint(100, 300)
    start_y = random.randint(100, 300)
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.1, 0.25))

    # Calculate steps for a smooth bezier-like approach
    steps = random.randint(15, 25)
    for step in range(1, steps + 1):
        t = step / steps
        # Add slight jitter to the path
        jitter_x = math.sin(t * math.pi) * random.uniform(-5, 5)
        jitter_y = math.sin(t * math.pi) * random.uniform(-5, 5)

        curr_x = start_x + (x - start_x) * t + jitter_x
        curr_y = start_y + (y - start_y) * t + jitter_y

        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(random.uniform(0.005, 0.015))

    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.12))
    await page.mouse.up()


async def _attempt_challenge_click(page: Any) -> bool:
    """Detect and attempt clicking the Cloudflare / Turnstile checkbox."""
    print(_getTime() + " - Detect WAF Challenge on page")
    # 1. Search inside embedded iframes (Cloudflare Turnstile standard pattern)
    for frame in page.frames:
        if "cloudflare" in frame.url or "turnstile" in frame.url or "challenges" in frame.url:
            print(_getTime() + " - Found iFrame based challenge")
            checkbox = frame.locator(
                'input[type="checkbox"], #challenge-stage, .ctp-checkbox-label'
            )
            if await checkbox.count() > 0:
                box = await checkbox.first.bounding_box()
                if box:
                    # Target center of the checkbox
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2
                    print(
                        f"{_getTime()} - Found Turnstile iframe checkbox. "
                        f"Clicking at ({target_x}, {target_y})"
                    )
                    await _human_move_and_click(page, target_x, target_y)
                    return True

    # 2. Search main page fallback (Custom WAF / inline checkbox)
    selectors = [
        'input[type="checkbox"]',
        "#challenge-stage",
        'button:has-text("Click to verify")',
        'div[role="checkbox"]',
        "#cf-stage",
        'div[id="captcha-box"]',
    ]
    for sel in selectors:
        elem = page.locator(sel)
        if await elem.count() > 0 and await elem.first.is_visible():
            print(_getTime() + " - Found Selector: " + sel)
            box = await elem.first.bounding_box()
            if box:
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2
                print(
                    f"{_getTime()} - Found challenge element ({sel}). "
                    f"Clicking at ({target_x}, {target_y})"
                )
                await _human_move_and_click(page, target_x, target_y)
                return True

    return False


async def save_session_state(
    context: object,
    user_agent: str,
    chrome_version: str,
    proxy_url: str | None = None,
    filepath: str = "session_state.json",
) -> None:
    """Persist the browser's cookies and headers for curl-cffi workers."""
    # Patchright's legacy context may call cookies without urls; both are safe.
    if hasattr(context, "cookies"):
        athome_urls = ["https://www.athome.co.jp"]
        raw_cookies = await context.cookies(urls=athome_urls)  # type: ignore[attr-defined]
    else:
        raw_cookies = await context.cookies()  # type: ignore[attr-defined]
    state = SessionState.from_browser(
        cookies=raw_cookies,
        user_agent=user_agent,
        chrome_version=chrome_version,
        proxy_url=proxy_url,
    )
    state.save(Path(filepath))
    print(f"{_getTime()} - Session State Saved: {filepath}")


async def _run(args: argparse.Namespace) -> None:
    """Launch the headed browser and wait for the operator's manual actions."""
    # Start the timer
    start_time = time.perf_counter()
    # args.debug_dir.mkdir(parents=True, exist_ok=True)
    _clearDebug(args.debug_dir)
    print(_getTime() + " - Prepare browser")
    # Store timing data for scripts
    script_timings = {}
    active_requests = {}

    async with async_playwright() as patchright:
        with tempfile.TemporaryDirectory(prefix="athome-patchright-manual-") as user_data_dir:
            # Track when a request starts
            def on_request(request):
                if (
                    request.resource_type == "fetch"
                    or request.resource_type == "script"
                    or request.url.endswith(".js")
                ):
                    # if "athome.co.jp" not in request.url:
                    active_requests[request.url] = time.time()

            # Track when the response finishes and compute duration
            def on_response(response):
                url = response.url
                if url in active_requests:
                    duration = (time.time() - active_requests[url]) * 1000  # in ms
                    script_timings[url] = duration

            chrome_ver = get_installed_chrome_version()
            launch_options: dict[str, object] = build_launch_options(
                user_data_dir=user_data_dir,
                chrome_version=chrome_ver,
                proxy_url=args.proxy,
            )
            if DEBUG:
                launch_options["record_video_dir"] = str(args.debug_dir)
                launch_options["record_video_size"] = {"width": 1280, "height": 720}
                print(
                    _getTime() + " - Using LaunchOptions: " + json.dumps(launch_options, indent=4)
                )

            context = await patchright.chromium.launch_persistent_context(
                **cast(Any, launch_options)
            )
            if DEBUG:
                await context.tracing.start(screenshots=True, snapshots=True, sources=False)

            page = await context.new_page()
            if DEBUG:
                set_route_logger(
                    lambda domain: print(
                        f"{_getTime()} - [intercept_route] Abort loading of resources from: "
                        + domain
                    )
                )
                page.on("request", on_request)
                page.on("response", on_response)
            await page.route("**/*", intercept_route)
            print(_getTime() + " - Ready browser, start loading page")
            try:
                # await stealth_async(page)
                await page.goto(args.url, wait_until="domcontentloaded")
                print(_getTime() + " - Page DomContent Loaded, waiting for selectors..")
                # Wait for whichever terminal DOM element attaches first
                # (challenge box or listing content); shared with the farmer.
                winning_element = await wait_for_page_signal(page)
                if winning_element and DEBUG:
                    # 1. Get the exact HTML signature of what popped up
                    element_id = await winning_element.evaluate("el => el.id || 'N/A'")
                    element_class = await winning_element.evaluate("el => el.className || 'N/A'")
                    element_tag = await winning_element.evaluate("el => el.tagName.toLowerCase()")

                    print(
                        f"{_getTime()} - RACE WON BY: <{element_tag} "
                        f"id='{element_id}' class='{element_class}'>"
                    )

                    # 2. Determine which logical branch we are in
                    if await page.locator(CHALLENGE_SELECTOR).count() > 0:
                        print(f"{_getTime()} - STATUS: WAF Challenge Detected.")
                    elif await page.locator(LISTING_SELECTOR).count() > 0:
                        print(f"{_getTime()} - STATUS: Clean Property Listing Detected.")
                print(_getTime() + " - Selectors found, waiting a little extra seconds..")
                # Since we are already waiting for selectors, this can be as little as 0.5s now
                # await asyncio.sleep(args.wait_seconds)
                await asyncio.sleep(0.5)
                print(_getTime() + " - Prepare beforeCapture")
                before_html = await _capture(page, args.debug_dir, "before")
                print(_getTime() + " - Capture Done")
                challenge_detected = detect_athome_challenge(before_html)
                print(f"{_getTime()} - Challenge detection status: {challenge_detected}")
                _event(
                    args.debug_dir,
                    "manual_before",
                    challenge_kind=challenge_detected,
                    html_chars=len(before_html),
                    body_sha256=hashlib.sha256(before_html.encode()).hexdigest(),
                    url=redact_url(page.url),
                )
                print(_getTime() + " - Event Recorded")

                # Handle challenge if present
                if challenge_detected:
                    print(
                        f"{_getTime()} - Challenge detected! "
                        f"Executing solve-mode: {args.solve_mode}"
                    )

                    if args.solve_mode == "click":
                        clicked = await _attempt_challenge_click(page)
                        if clicked:
                            print(f"{_getTime()} - Click dispatched, waiting for verification...")
                            await asyncio.sleep(5.0)
                        else:
                            print(f"{_getTime()} - Could not find verification target to click.")

                    elif args.solve_mode == "capsolver":
                        if not args.capsolver_key:
                            print(f"{_getTime()} - Error: CapSolver API key not provided.")
                        elif challenge_detected == "puzzle":
                            # 1. Extract Geetest keys AND Incapsula's binding
                            #    data string from the raw HTML
                            gt_match = re.search(r'gt:\s*["\']([^"\']+)["\']', before_html)
                            challenge_match = re.search(
                                r'challenge:\s*["\']([^"\']+)["\']', before_html
                            )
                            data_match = re.search(r'data:\s*["\'](3:[^"\']+)["\']', before_html)

                            if gt_match and challenge_match and data_match:
                                gt = gt_match.group(1)
                                challenge = challenge_match.group(1)
                                incapsula_data = data_match.group(1)

                                print(
                                    f"{_getTime()} - Extracted Geetest Params. "
                                    "Sending to CapSolver..."
                                )
                                solution = await solve_geetest_capsolver(
                                    args.capsolver_key, page.url, gt, challenge
                                )

                                if solution:
                                    # 2. Format payload exactly as Incapsula's solvedCaptcha expects
                                    payload = {
                                        "geetest_challenge": solution.get("challenge"),
                                        "geetest_validate": solution.get("validate"),
                                        "geetest_seccode": solution.get("seccode"),
                                        "data": incapsula_data,
                                    }
                                    print(
                                        f"{_getTime()} - Injecting Geetest payload "
                                        "via solvedCaptcha..."
                                    )
                                    # 3. Fire the payload directly into the global WAF function
                                    await page.evaluate(f"solvedCaptcha({json.dumps(payload)})")

                                    print(
                                        f"{_getTime()} - Wait for WAF to validate "
                                        "and reload page..."
                                    )
                                    await asyncio.sleep(5.0)
                            else:
                                print(
                                    f"{_getTime()} - Error: Could not extract gt, "
                                    "challenge, or data keys from DOM."
                                )
                        else:
                            # Extract Turnstile sitekey if present
                            site_key = await page.evaluate(
                                "() => document.querySelector('[data-sitekey]')"
                                "?.getAttribute('data-sitekey') || ''"
                            )
                            if site_key:
                                token = await solve_turnstile_capsolver(
                                    args.capsolver_key, page.url, site_key
                                )
                                if token:
                                    # Inject token back into DOM
                                    await page.evaluate(
                                        """
                                        (token) => {
                                            const input =
                                                document.querySelector('input[name="cf-turnstile-response"]')
                                                || document.createElement('input');
                                            input.value = token;
                                            document.forms[0]?.submit();
                                        }
                                    """,
                                        token,
                                    )
                                    await asyncio.sleep(5.0)

                print(
                    _getTime()
                    + " - Browser is ready for manual observation. You may inspect and interact "
                    "with the page, then press Enter here to finish."
                )
                # await asyncio.to_thread(input)
                print(_getTime() + " - Prepare afterCapture")
                after_html = await _capture(page, args.debug_dir, "after")
                print(_getTime() + " - Capture done")
                challenge_detected = detect_athome_challenge(after_html)
                print(f"{_getTime()} - Challenge detection status: {challenge_detected}")
                if challenge_detected is None:
                    # Save request headers
                    # playwright_cookies = await page.context.cookies()
                    # Convert Playwright cookie format to a standard dictionary
                    # cffi_cookies = {
                    #     cookie['name']: cookie['value'] for cookie in playwright_cookies
                    # }
                    # Grab the User-Agent you used in Playwright
                    user_agent = await page.evaluate("navigator.userAgent")
                    await save_session_state(
                        page.context,
                        user_agent,
                        chrome_ver,
                        args.proxy,
                        filepath=str(args.debug_dir / "session_state.json"),
                    )
                _event(
                    args.debug_dir,
                    "manual_after",
                    challenge_kind=challenge_detected,
                    html_chars=len(after_html),
                    body_sha256=hashlib.sha256(after_html.encode()).hexdigest(),
                    url=redact_url(page.url),
                )
                print(_getTime() + " - Event recorded")
            finally:
                video = None
                if DEBUG:
                    await context.tracing.stop(
                        path=str(args.debug_dir / "playwright_challenge_trace.zip")
                    )
                    print(_getTime() + " - Trace zip saved")
                    video = page.video
                    print(_getTime() + " - Video fetched")
                await context.close()
                print(_getTime() + " - Browser context closed")
                if video is not None:
                    generated_video = Path(await video.path())
                    target_video = args.debug_dir / "playwright_challenge.webm"
                    if generated_video != target_video and generated_video.exists():
                        generated_video.replace(target_video)
                        print(_getTime() + " - Video Saved")
    print("" + _getTime() + f"- Diagnostics saved under {args.debug_dir}/")
    # End the timer
    end_time = time.perf_counter()
    # Calculate elapsed time
    execution_time = end_time - start_time
    if DEBUG:
        # Sort scripts by duration (slowest first)
        sorted_scripts = sorted(script_timings.items(), key=lambda x: x[1], reverse=True)
        print("\n--- Resources Load Times (Slowest to Fastest) ---")
        for url, duration in sorted_scripts:
            print(f"{duration:.2f} ms : {url}")

    print(f"Execution time: {execution_time:.6f} seconds")


def main() -> None:
    """Parse arguments and run the headed manual probe."""
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
