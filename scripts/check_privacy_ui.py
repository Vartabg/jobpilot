"""CI interaction and fail-closed accessibility checks for the privacy scan.

Run with --axe /path/to/axe.min.js. The only data used is synthetic.
"""

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def verify(base, executable=None):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=executable)
        page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        await page.goto(base + "/capture-fixture?token=PRIVATE_TOKEN")
        await page.get_by_role("button", name="Capture visible field metadata").click()
        text = await page.get_by_label("Captured field metadata").input_value()
        assert "PRIVATE" not in text and "private@example" not in text
        captured = json.loads(text)
        assert len([f for f in captured["fields"] if f["label"] == "Pronouns"]) == 1
        assert next(f for f in captured["fields"] if f["label"] == "Pronouns")["required"] is True
        await page.keyboard.press("Escape")
        assert await page.get_by_role("button", name="Capture visible field metadata").evaluate("el=>el===document.activeElement")
        assert await page.locator("#submissions").inner_text() == "0 submissions"
        await page.goto(base + "/privacy-audit")
        assert await page.locator("main").count() == 1 and await page.locator("h1").count() == 1
        await page.get_by_role("link", name="Skip to content").focus()
        await page.keyboard.press("Enter")
        assert await page.locator("main").evaluate("el=>el===document.activeElement")
        await page.get_by_label("Job links").fill("https://example.test/jobs/one\nhttps://blocked.example/jobs/two")
        await page.get_by_role("button", name="Scan job links", exact=True).focus()
        await page.keyboard.press("Enter")
        await page.get_by_role("heading", name="Your scan results", exact=True).wait_for()
        assert "1 of 2" in await page.locator("#result-summary").inner_text()
        assert "Response required; disclosure optional" in await page.locator("#reports").inner_text()
        await page.get_by_label("Show", exact=True).select_option("required")
        assert await page.locator(".finding").count() == 1
        await page.get_by_text("Analyze captured field metadata", exact=True).click()
        await page.get_by_label("Captured field metadata").fill(text)
        await page.get_by_role("button", name="Analyze captured questions").click()
        await page.locator("#result-summary").filter(has_text="2 of 3").wait_for()
        await page.get_by_label("Show", exact=True).select_option("all")
        await page.get_by_role("button", name="Run accessibility audit").click()
        await page.locator("#audit-results").filter(has_text="violations").wait_for()
        result = json.loads(await page.locator("#audit-results").inner_text())
        assert result.get("violations") == [] and result.get("passes", 0) > 0, result
        await page.get_by_label("Captured field metadata").fill('{"fields":[{"label":"Gender","answer":"PRIVATE"}]}')
        await page.get_by_role("button", name="Analyze captured questions").click()
        await page.locator("#error").filter(has_text="answer fields").wait_for()
        for width in (640, 320):
            await page.set_viewport_size({"width": width, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Overflow at {width}px"
        await page.emulate_media(forced_colors="active", reduced_motion="reduce")
        await page.get_by_role("button", name="Run accessibility audit").click()
        await page.locator("#audit-results").filter(has_text="violations").wait_for()
        result = json.loads(await page.locator("#audit-results").inner_text())
        assert result.get("violations") == [] and result.get("passes", 0) > 0, result
        await page.get_by_label("Show", exact=True).focus()
        await page.keyboard.press("Shift+Tab")
        assert await page.locator("#download-report").evaluate("el=>el===document.activeElement")
        assert not errors, errors
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axe", type=Path, required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    if not args.axe.is_file():
        raise SystemExit("axe-core is required; missing audits must fail.")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="jobpilot-privacy-check-") as root:
        proc = subprocess.Popen([sys.executable, str(ROOT / "tests/product/serve_privacy_fixture.py"), "--port", str(port), "--data-dir", root, "--axe", str(args.axe)], cwd=ROOT)
        try:
            for _ in range(100):
                try:
                    if requests.get(base + "/health", timeout=0.2).ok:
                        break
                except requests.ConnectionError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("The synthetic privacy check server did not start.")
            asyncio.run(verify(base, args.executable))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    print("PASS: privacy capture, comparison, keyboard, accessibility, and reflow")


if __name__ == "__main__":
    main()
