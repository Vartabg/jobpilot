import asyncio
import json
import sys

from playwright.async_api import async_playwright


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "brettonai"
    port = sys.argv[2] if len(sys.argv) > 2 else "9223"

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
        data = await page.evaluate(
            """() => {
              const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
              const labelFor = (el) => {
                const id = el.id;
                if (id) {
                  const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
                  if (lab) return lab.innerText.trim();
                }
                const wrapper = el.closest('label,[data-testid],div');
                return wrapper ? wrapper.innerText.trim().slice(0, 300) : '';
              };
              const fields = [...document.querySelectorAll('input, textarea, select')]
                .filter(visible)
                .map(el => ({
                  tag: el.tagName.toLowerCase(),
                  type: el.getAttribute('type') || '',
                  name: el.getAttribute('name') || '',
                  aria: el.getAttribute('aria-label') || '',
                  required: el.required || el.getAttribute('aria-required') === 'true',
                  value: el.type === 'file'
                    ? (el.files && el.files.length ? [...el.files].map(f => f.name).join(', ') : '')
                    : (el.value || ''),
                  checked: !!el.checked,
                  label: labelFor(el)
                }));
              const buttons = [...document.querySelectorAll('button')]
                .filter(visible)
                .map(b => ({
                  text: b.innerText.trim(),
                  disabled: b.disabled,
                  ariaPressed: b.getAttribute('aria-pressed') || '',
                  ariaChecked: b.getAttribute('aria-checked') || '',
                  className: b.className || '',
                  parentText: b.parentElement ? b.parentElement.innerText.trim().slice(0, 400) : ''
                }));
              const bodyText = document.body.innerText;
              return {
                url: location.href,
                title: document.title,
                fields,
                buttons,
                hasSuccess: /successfully submitted|application was submitted|thank you/i.test(bodyText),
                bodyTail: bodyText.slice(-1800)
              };
            }"""
        )
        print(json.dumps(data, indent=2))
        await browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
