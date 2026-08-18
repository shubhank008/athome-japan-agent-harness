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
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from datetime import datetime
import os
import shutil
import math
import random
import aiohttp
import re
import json

from patchright.async_api import async_playwright

from athome_harness.scraping.base import redact_url
from athome_harness.scraping.challenge import detect_athome_challenge

try:
    from playwright_stealth import stealth_async
except ImportError:
    from playwright_stealth import Stealth  # type: ignore[import-untyped]

    async def stealth_async(page: object) -> None:
        """Apply the current playwright-stealth API under the legacy name."""
        await Stealth().apply_stealth_async(page)


DEFAULT_URL = "https://www.athome.co.jp/chintai/osaka/list/"


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
        help="Challenge handling strategy: 'none' (observe only), 'click' (auto-interact), 'capsolver' (API solver)",
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
    # Screenshot first so we know what the browser is seeing
    await page.screenshot(path=str(debug_dir / f"playwright_{stage}.png"))  # type: ignore[attr-defined]
    html = await page.content()  # type: ignore[attr-defined]
    (debug_dir / f"playwright_{stage}.html").write_text(html, encoding="utf-8")
    return cast(str, html)

def _getTime() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _clearDebug(folder_path: str) -> None:
    # Delete the entire folder and all its contents
    shutil.rmtree(folder_path)
    # Recreate the empty folder
    os.makedirs(folder_path)
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
            checkbox = frame.locator('input[type="checkbox"], #challenge-stage, .ctp-checkbox-label')
            if await checkbox.count() > 0:
                box = await checkbox.first.bounding_box()
                if box:
                    # Target center of the checkbox
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2
                    print(f"{_getTime()} - Found Turnstile iframe checkbox. Clicking at ({target_x}, {target_y})")
                    await _human_move_and_click(page, target_x, target_y)
                    return True

    # 2. Search main page fallback (Custom WAF / inline checkbox)
    selectors = [
        'input[type="checkbox"]',
        '#challenge-stage',
        'button:has-text("Click to verify")',
        'div[role="checkbox"]',
        '#cf-stage',
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
                print(f"{_getTime()} - Found challenge element ({sel}). Clicking at ({target_x}, {target_y})")
                await _human_move_and_click(page, target_x, target_y)
                return True

    return False


async def _solve_turnstile_capsolver(api_key: str, site_url: str, site_key: str) -> str | None:
    """Solve Cloudflare Turnstile using CapSolver API."""
    print(f"{_getTime()} - Requesting CapSolver token for {site_url}")
    endpoint = "https://api.capsolver.com/createTask"
    
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": site_url,
            "websiteKey": site_key,
        },
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()
            if data.get("errorId", 0) != 0:
                print(f"{_getTime()} - CapSolver createTask error: {data.get('errorDescription')}")
                return None
            task_id = data.get("taskId")

        # Poll result
        result_url = "https://api.capsolver.com/getTaskResult"
        for _ in range(30):
            await asyncio.sleep(2)
            async with session.post(result_url, json={"clientKey": api_key, "taskId": task_id}) as res_resp:
                res_data = await res_resp.json()
                if res_data.get("status") == "ready":
                    print(f"{_getTime()} - CapSolver token received!")
                    return res_data["solution"]["token"]
                if res_data.get("status") == "failed":
                    print(f"{_getTime()} - CapSolver failed to solve challenge.")
                    return None
    return None


async def _solve_geetest_capsolver(api_key: str, site_url: str, gt: str, challenge: str) -> dict | None:
    """Solve Geetest V3 using CapSolver API."""
    print(f"{_getTime()} - Requesting CapSolver token for Geetest (gt: {gt[:5]}...)")
    endpoint = "https://api.capsolver.com/createTask"
    
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "GeeTestTaskProxyLess",
            "websiteURL": site_url,
            "gt": gt,
            "challenge": challenge,
        },
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()
            if data.get("errorId", 0) != 0:
                print(f"{_getTime()} - CapSolver createTask error: {data.get('errorDescription')}")
                return None
            task_id = data.get("taskId")

        # Poll result
        result_url = "https://api.capsolver.com/getTaskResult"
        for _ in range(30):  # Poll for up to 60 seconds
            await asyncio.sleep(2)
            async with session.post(result_url, json={"clientKey": api_key, "taskId": task_id}) as res_resp:
                res_data = await res_resp.json()
                if res_data.get("status") == "ready":
                    print(f"{_getTime()} - CapSolver token received!")
                    return res_data["solution"]  # Returns dict with challenge, validate, seccode
                if res_data.get("status") == "failed":
                    print(f"{_getTime()} - CapSolver failed to solve challenge.")
                    return None
    return None

async def _run(args: argparse.Namespace) -> None:
    """Launch the headed browser and wait for the operator's manual actions."""
    #args.debug_dir.mkdir(parents=True, exist_ok=True)
    _clearDebug(args.debug_dir)
    print(_getTime() + " - Prepare browser")
    async with async_playwright() as patchright:
        with tempfile.TemporaryDirectory(prefix="athome-patchright-manual-") as user_data_dir:
            launch_options: dict[str, object] = {
                "user_data_dir": user_data_dir,
                "channel": "chrome",
                "headless": True,
                #"no_viewport": True,
                "viewport": {"width": 1920, "height": 1080},
                "locale": "ja-JP",
                "timezone_id": "Asia/Tokyo",
                "record_video_dir": str(args.debug_dir),
                "record_video_size": {"width": 1280, "height": 720},
                "args": [
                    "--window-size=1920,1080",
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=ja-JP",
                ],
            }
            if args.proxy:
                launch_options["proxy"] = {"server": args.proxy}
            context = await patchright.chromium.launch_persistent_context(
                **cast(Any, launch_options)
            )
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            page = await context.new_page()
            print(_getTime() + " - Ready browser")
            try:
                #await stealth_async(page)
                await page.goto(args.url, wait_until="domcontentloaded")
                print(_getTime() + " - Page Loaded, waiting..")
                await asyncio.sleep(args.wait_seconds)
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
                    print(f"{_getTime()} - Challenge detected! Executing solve-mode: {args.solve_mode}")
                    
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
                        else if challenge_detected == "puzzle":
                            # 1. Extract Geetest keys AND Incapsula's binding data string from the raw HTML
                            gt_match = re.search(r'gt:\s*["\']([^"\']+)["\']', before_html)
                            challenge_match = re.search(r'challenge:\s*["\']([^"\']+)["\']', before_html)
                            data_match = re.search(r'data:\s*["\'](3:[^"\']+)["\']', before_html)
                            
                            if gt_match and challenge_match and data_match:
                                gt = gt_match.group(1)
                                challenge = challenge_match.group(1)
                                incapsula_data = data_match.group(1)
                                
                                print(f"{_getTime()} - Extracted Geetest Params. Sending to CapSolver...")
                                solution = await _solve_geetest_capsolver(args.capsolver_key, page.url, gt, challenge)
                                
                                if solution:
                                    # 2. Format payload exactly as Incapsula's solvedCaptcha expects
                                    payload = {
                                        "geetest_challenge": solution.get("challenge"),
                                        "geetest_validate": solution.get("validate"),
                                        "geetest_seccode": solution.get("seccode"),
                                        "data": incapsula_data
                                    }
                                    print(f"{_getTime()} - Injecting Geetest payload via solvedCaptcha...")
                                    # 3. Fire the payload directly into the global WAF function
                                    await page.evaluate(f"solvedCaptcha({json.dumps(payload)})")
                                    
                                    print(f"{_getTime()} - Wait for WAF to validate and reload page...")
                                    await asyncio.sleep(5.0) 
                            else:
                                print(f"{_getTime()} - Error: Could not extract gt, challenge, or data keys from DOM.")
                        else:
                            # Extract Turnstile sitekey if present
                            site_key = await page.evaluate(
                                "() => document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey') || ''"
                            )
                            if site_key:
                                token = await _solve_turnstile_capsolver(args.capsolver_key, page.url, site_key)
                                if token:
                                    # Inject token back into DOM
                                    await page.evaluate(f"""
                                        (token) => {{
                                            const input = document.querySelector('input[name="cf-turnstile-response"]') || document.createElement('input');
                                            input.value = token;
                                            document.forms[0]?.submit();
                                        }}
                                    """, token)
                                    await asyncio.sleep(5.0)

                print(_getTime() + 
                    "Browser is ready for manual observation. You may inspect and interact "
                    "with the page, then press Enter here to finish."
                )
                await asyncio.to_thread(input)
                print(_getTime() + " - Prepare afterCapture")
                after_html = await _capture(page, args.debug_dir, "after")
                print(_getTime() + " - Capture done")
                challenge_detected = detect_athome_challenge(after_html)
                print(f"{_getTime()} - Challenge detection status: {challenge_detected}")
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
                await context.tracing.stop(
                    path=str(args.debug_dir / "playwright_challenge_trace.zip")
                )
                print(_getTime() + " - Trace zip saved")
                video = page.video
                await context.close()
                print(_getTime() + " - Video fetched, browser context closed")
                if video is not None:
                    generated_video = Path(await video.path())
                    target_video = args.debug_dir / "playwright_challenge.webm"
                    if generated_video != target_video and generated_video.exists():
                        generated_video.replace(target_video)
                        print(_getTime() + " - Video Saved")
    print(f"" +_getTime()+ "- Diagnostics saved under {args.debug_dir}/")


def main() -> None:
    """Parse arguments and run the headed manual probe."""
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
