"""Fail-closed dashboard interaction and accessibility checks using synthetic data.

Run: python scripts/check_ui.py --axe /path/to/axe-core/axe.min.js
On macOS pass --executable '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'.
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import requests
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "browser-check-only"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def audit(page, axe, label):
    await page.add_script_tag(path=str(axe))
    results = await page.evaluate("axe.run(document, {runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa','best-practice']}})")
    failures = [{"id": v["id"], "nodes": [n["target"] for n in v["nodes"]]} for v in results["violations"]]
    assert not failures, f"{label}: {json.dumps(failures)}"
    assert await page.locator("main").count() == 1
    assert await page.locator("h1").count() == 1
    assert await page.get_by_role("link", name="Skip", exact=False).count() == 1
    print(f"PASS accessibility: {label}", flush=True)


async def verify(args, core_url, gigs_url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=args.executable, headless=True,
            ignore_default_args=["--disable-popup-blocking"],
        )
        context = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = await page.goto(core_url)
        assert response.status == 401
        await page.goto(f"{core_url}?token={TOKEN}")
        await page.wait_for_load_state("networkidle")
        assert "token=" not in page.url
        assert TOKEN not in await page.content()
        await audit(page, args.axe, "dashboard")
        await page.get_by_role("link", name="Fill Button", exact=True).focus()
        await page.keyboard.press("Enter")
        await page.get_by_role("heading", name="Install the Fill Button", exact=False).wait_for()
        await audit(page, args.axe, "install")
        await page.goto(gigs_url)  # same authenticated host, second server
        await audit(page, args.axe, "swipe start")
        await page.get_by_role("link", name="Skip to jobs").focus()
        await page.keyboard.press("Enter")
        assert await page.locator("main").evaluate("el => el === document.activeElement")
        await page.get_by_role("button", name="Get jobs").focus()
        await page.keyboard.press("Enter")
        await page.locator("#card").wait_for()
        await audit(page, args.axe, "swipe card")
        async with page.expect_popup() as popup_info:
            await page.get_by_role("button", name="★ Apply", exact=True).focus()
            await page.keyboard.press("Enter")
        popup = await popup_info.value
        await popup.wait_for_url(core_url, timeout=15000)
        assert await popup.evaluate("window.opener === null")
        await page.get_by_text("Application opened", exact=True).wait_for()
        print("PASS: keyboard apply survives a six-second save with popup blocking enabled", flush=True)
        await popup.close()
        await page.reload()
        await page.get_by_role("button", name="Get jobs").click()
        await page.locator("#card").wait_for()
        await page.evaluate("cards[0].is_mailto = true; cards[0].apply_target = 'mailto:test@invalid.test'; render()")
        await page.get_by_role("button", name="★ Apply", exact=True).click()
        link = page.get_by_role("link", name="Open email draft", exact=True)
        await link.wait_for(timeout=15000)
        assert await link.evaluate("el => el === document.activeElement")
        await page.wait_for_timeout(4200)
        assert await link.is_visible()
        await audit(page, args.axe, "persistent email action")
        for width in (195, 320, 780):
            await page.set_viewport_size({"width": width, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"overflow at {width}px"
        await page.emulate_media(reduced_motion="reduce", forced_colors="active")
        assert await page.locator(".spinner").evaluate("el => getComputedStyle(el).animationName") == "none"
        await page.get_by_role("button", name="Undo", exact=True).focus()
        await page.keyboard.press("Shift+Tab")
        assert await link.evaluate("el => el === document.activeElement")
        assert not errors, errors
        print("PASS: persistent mail link, reverse keyboard order, reflow, reduced motion, forced colors, no browser errors", flush=True)
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path, required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    assert args.axe.is_file(), "axe-core is required; missing audits must fail"
    core_port, gigs_port = free_port(), free_port()
    core_url, gigs_url = f"http://127.0.0.1:{core_port}/", f"http://127.0.0.1:{gigs_port}/"
    env = dict(os.environ, PYTHONPATH=str(ROOT.parent), JOBPILOT_SERVER_TOKEN=TOKEN)
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests/browser/server.py"), str(core_port), str(gigs_port)], env=env)
    try:
        import time
        for _ in range(100):
            try:
                if requests.get(gigs_url, timeout=0.2).status_code == 401:
                    break
            except requests.ConnectionError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("UI test server did not start")
        asyncio.run(verify(args, core_url, gigs_url))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
