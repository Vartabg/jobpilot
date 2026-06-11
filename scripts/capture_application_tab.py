import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main() -> int:
    target = sys.argv[1]
    output = Path(sys.argv[2])
    port = sys.argv[3] if len(sys.argv) > 3 else "9223"

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if target in pg.url:
                    page = pg
                    break
            if page:
                break

        if page is None:
            print("NO_MATCHING_PAGE")
            await browser.close()
            return 1

        await page.wait_for_load_state("domcontentloaded")
        output.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output), full_page=True)
        print(output)
        await browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
