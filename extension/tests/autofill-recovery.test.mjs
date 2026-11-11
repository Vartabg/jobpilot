import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from '@playwright/test';

// Intercepted, synthetic employer forms: no real applications or installed Chrome profiles.
const script = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const profile = {
  first_name: 'Alex', last_name: 'Example', email: 'alex@example.org',
  phone: '+1 512 555 0100', city: 'Austin', state: 'TX', country: 'United States',
};
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function fixture(html, run) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/*', route => route.request().isNavigationRequest()
    ? route.fulfill({ contentType: 'text/html', body: html }) : route.abort());
  await page.goto('https://job-boards.greenhouse.io/example/jobs/123');
  await page.addScriptTag({ content: script });
  try { return await run(page); } finally { await context.close(); }
}
const execute = (page, action = 'details', overrides = {}) => page.evaluate(
  options => globalThis.__jobpilotRun(options), { action, profile: { ...profile, ...overrides } },
);

function selectControl({ id, label, options = '', extra = '' }) {
  return `<div class="field"><label id="${id}-label" for="${id}">${label}</label>
    <div class="select__control"><input id="${id}" role="combobox" aria-labelledby="${id}-label"
      aria-controls="react-select-${id}-listbox" aria-expanded="false" aria-required="true">
      <span class="select__single-value"></span></div>
    <div id="react-select-${id}-listbox" role="listbox">${options}</div></div>${extra}`;
}
function selectionHandler(id) {
  return `document.querySelector('#react-select-${id}-listbox').addEventListener('click', event => {
    const option = event.target.closest('[role="option"]');
    if (!option) return;
    const input = document.querySelector('#${id}');
    input.closest('.select__control').querySelector('.select__single-value').textContent = option.textContent;
    input.value = ''; input.setAttribute('aria-expanded', 'false');
  });`;
}

test('Greenhouse city lookup waits for delayed suggestions and matches the complete saved location', async () => {
  await fixture(selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    options: '<div role="option">Berlin, Berlin, Germany</div>',
    extra: `<script>
      ${selectionHandler('candidate-location')}
      let requested = false;
      document.querySelector('#candidate-location').addEventListener('input', () => {
        if (requested) return; requested = true;
        setTimeout(() => {
          document.querySelector('#react-select-candidate-location-listbox').innerHTML =
            '<div role="option">Austin Gardens Colonia, Texas, United States</div><div role="option">Austin, Texas, United States</div>';
        }, 1200);
      });
    </script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'Austin, Texas, United States');
    assert.ok(result.filled.some(field => /location/i.test(field.label)));
    assert.equal(result.required.length, 0);
    assert.equal(result.failed.length, 0);
  });
});

test('a dropdown rerender fills the replacement input rather than claiming a detached input succeeded', async () => {
  await fixture(`<label>Country<select id="country"><option value="">Choose</option><option>United States</option></select></label>
    <label>First name<input id="first_name" required></label>
    <script>document.querySelector('#country').addEventListener('change', () => {
      const input = document.querySelector('#first_name'); input.replaceWith(input.cloneNode(true));
    });</script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#first_name').inputValue(), 'Alex');
    assert.ok(result.filled.some(field => field.key === 'first_name'));
    assert.ok(!result.required.some(field => /first name/i.test(field.label)));
  });
});

test('a confirmed USA country selection is accepted using the same aliases as option matching', async () => {
  await fixture(selectControl({
    id: 'country', label: 'Country *', options: '<div role="option">USA</div>',
    extra: `<script>${selectionHandler('country')}</script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'USA');
    assert.equal(result.failed.length, 0);
    assert.ok(result.filled.some(field => field.key === 'country'));
  });
});

test('phone country matches a unique country plus dial prefix and preserves the applicant phone', async () => {
  await fixture(`<div class="phone-input"><span>Phone</span>${selectControl({
    id: 'country', label: 'Country *', options: '<div role="option">United States +1</div><div role="option">Canada +1</div>',
  })}<label>Phone<input id="phone" type="tel" value="512-555-9999" required></label></div>
    <script>${selectionHandler('country')}</script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'United States +1');
    assert.equal(await page.locator('#phone').inputValue(), '512-555-9999');
    assert.equal(result.failed.length, 0);
  });
});

test('a generic Country control in a shared phone container fills the code and national number', async () => {
  await fixture(`<div class="phone-input">${selectControl({
    id: 'country', label: 'Country *',
    options: '<div role="option">United States +1</div><div role="option">Canada +1</div>',
  })}<label>Phone<input id="phone" type="tel"></label></div>
    <script>${selectionHandler('country')}</script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'United States +1');
    assert.equal(await page.locator('#phone').inputValue(), '512 555 0100');
    assert.ok(result.filled.some(field => field.key === 'phone_country_code'));
    assert.ok(result.filled.some(field => field.key === 'phone'));
  });
});

test('an unrelated open listbox is never used for an unassociated location dropdown', async () => {
  await fixture(`<label>Location<input id="location" role="combobox" aria-controls="missing-listbox" aria-required="true"></label>
    <div id="other-listbox" role="listbox"><div role="option">Austin, TX</div></div>
    <script>document.querySelector('[role="option"]').addEventListener('click', () => document.body.dataset.unrelatedClicked = 'true');</script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('body').getAttribute('data-unrelated-clicked'), null);
    assert.equal(await page.locator('#location').inputValue(), '');
    assert.equal(result.filled.length, 0);
    assert.ok(result.required.some(field => /location/i.test(field.label)));
  });
});

test('ambiguous same-city suggestions remain unanswered instead of selecting the first one', async () => {
  await fixture(selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    options: '<div id="place-a" role="option">Austin, Texas, United States</div><div id="place-b" role="option">Austin, Texas, United States</div>',
    extra: `<script>${selectionHandler('candidate-location')}</script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), '');
    assert.equal(result.filled.length, 0);
    assert.ok(result.required.some(field => /location/i.test(field.label)));
  });
});

test('a delayed dependent state field is discovered and filled during the same action', async () => {
  await fixture(`<label>Country<select id="country"><option value="">Choose</option><option>United States</option></select></label>
    <div id="dependent"></div><script>
      document.querySelector('#country').addEventListener('change', () => setTimeout(() => {
        document.querySelector('#dependent').innerHTML = '<label>State<input id="state" required></label>';
      }, 350));
    </script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#state').inputValue(), 'TX');
    assert.ok(result.filled.some(field => field.key === 'state'));
    assert.ok(!result.required.some(field => /state/i.test(field.label)));
  });
});

test('an applicant answer entered while another dropdown waits is preserved', async () => {
  await fixture(selectControl({
    id: 'country', label: 'Country *',
    extra: `<label>First name<input id="first_name" required></label><script>
      ${selectionHandler('country')}
      let requested = false;
      document.querySelector('#country').addEventListener('input', () => {
        if (requested) return; requested = true;
        setTimeout(() => { document.querySelector('#first_name').value = 'Applicant correction'; }, 150);
        setTimeout(() => {
          document.querySelector('#react-select-country-listbox').innerHTML = '<div role="option">United States</div>';
        }, 450);
      });
    </script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#first_name').inputValue(), 'Applicant correction');
    assert.ok(result.skipped.some(field => /first name/i.test(field.label) && /preserv/i.test(field.reason)));
  });
});

test('a manual country selection made during lookup is preserved', async () => {
  await fixture(selectControl({
    id: 'country', label: 'Country *',
    extra: `<script>
      ${selectionHandler('country')}
      let requested = false;
      document.querySelector('#country').addEventListener('input', () => {
        if (requested) return; requested = true;
        setTimeout(() => {
          document.querySelector('.select__single-value').textContent = 'Canada';
          document.querySelector('#country').value = '';
        }, 150);
        setTimeout(() => {
          document.querySelector('#react-select-country-listbox').innerHTML = '<div role="option">United States</div>';
        }, 450);
      });
    </script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'Canada');
    assert.equal(result.filled.length, 0);
    assert.ok(result.skipped.some(field => /country/i.test(field.label) && /preserv/i.test(field.reason)));
  });
});

test('a manual dropdown query entered during lookup remains unchanged and open', async () => {
  await fixture(selectControl({
    id: 'country', label: 'Country *',
    extra: `<script>
      ${selectionHandler('country')}
      let requested = false;
      const input = document.querySelector('#country');
      input.addEventListener('keydown', event => { if (event.key === 'Escape') document.body.dataset.escaped = 'true'; });
      input.addEventListener('input', () => {
        if (requested) return; requested = true;
        setTimeout(() => { input.value = 'Ca'; input.setAttribute('aria-expanded', 'true'); }, 150);
        setTimeout(() => {
          document.querySelector('#react-select-country-listbox').innerHTML = '<div role="option">United States</div>';
        }, 450);
      });
    </script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#country').inputValue(), 'Ca');
    assert.equal(await page.locator('#country').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('body').getAttribute('data-escaped'), null);
    assert.equal(await page.locator('.select__single-value').textContent(), '');
    assert.equal(result.filled.length, 0);
    assert.ok(result.skipped.some(field => /preserv/i.test(field.reason)));
  });
});

test('an existing pending city search is preserved and remains required until selected', async () => {
  await fixture(selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    options: '<div role="option">Austin, Texas, United States</div>',
    extra: `<script>
      ${selectionHandler('candidate-location')}
      document.querySelector('#candidate-location').value = 'Austin Gardens';
      document.querySelector('#candidate-location').setAttribute('aria-expanded', 'true');
    </script>`,
  }), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#candidate-location').inputValue(), 'Austin Gardens');
    assert.equal(await page.locator('#candidate-location').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('.select__single-value').textContent(), '');
    assert.equal(result.filled.length, 0);
    assert.ok(result.required.some(field => /location/i.test(field.label)));
    assert.ok(result.skipped.some(field => /preserv/i.test(field.reason)));
  });
});

test('nested Greenhouse Attach input inherits the required Resume/CV field context', async () => {
  await fixture(`<div class="file-upload"><label>Resume/CV *</label><div class="file-upload__wrapper">
    <div class="button-container"><div class="secondary-button"><label for="resume">Attach</label>
    <div><input id="resume" type="file"></div></div></div></div></div>`, async page => {
    const result = await execute(page, 'inspect');
    assert.ok(result.required.some(field => /resume|cv/i.test(field.label) && field.type === 'file'));
    assert.equal(result.filled.length, 0);
  });
});

test('a phone value rejected by the native pattern is reported as needing review', async () => {
  await fixture('<label>Phone<input id="phone" type="tel" pattern="[0-9]{10}" required></label>', async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#phone').evaluate(input => input.validity.patternMismatch), true);
    assert.equal(result.filled.length, 0);
    assert.equal(result.status, 'partial');
    assert.ok(result.failed.some(field => /phone/i.test(field.label)));
  });
});

const nativeSplitPhone = (options = '<option value="us">+1</option><option value="gb">+44</option>') => `
  <div class="phone-input"><label>Country code<select id="country-code" autocomplete="tel-country-code">
    <option value="">Choose</option>${options}</select></label>
    <label>Phone<input id="phone" type="tel" autocomplete="tel-national" required></label></div>`;

test('a dedicated Greenhouse country code and national phone fill from one explicit international number', async () => {
  await fixture(nativeSplitPhone(), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#country-code').inputValue(), 'us');
    assert.equal(await page.locator('#phone').inputValue(), '512 555 0100');
    assert.ok(result.filled.some(field => field.key === 'phone_country_code'));
    assert.ok(result.filled.some(field => field.key === 'phone'));
    assert.equal(result.failed.length, 0);
  });
});

test('the split-phone parser supports a clearly separated multi-digit prefix', async () => {
  await fixture(nativeSplitPhone(), async page => {
    const result = await execute(page, 'details', { phone: '+44 20 7946 0100', country: 'United Kingdom' });
    assert.equal(await page.locator('#country-code').inputValue(), 'gb');
    assert.equal(await page.locator('#phone').inputValue(), '20 7946 0100');
    assert.equal(result.failed.length, 0);
  });
});

test('ambiguous or unsupported split-phone formats write neither part', async () => {
  for (const phone of ['512 555 0100', '+4420 7946 0100', '+1 512 555 0100 ext 9']) {
    await fixture(nativeSplitPhone(), async page => {
      const result = await execute(page, 'details', { phone });
      assert.equal(await page.locator('#country-code').inputValue(), '', phone);
      assert.equal(await page.locator('#phone').inputValue(), '', phone);
      assert.equal(result.filled.some(field => field.key === 'phone'), false);
      assert.equal(result.filled.some(field => field.key === 'phone_country_code'), false);
    });
  }
});

test('a named phone-country combobox requires both the saved country and dial prefix', async () => {
  await fixture(selectControl({
    id: 'phone-country', label: 'Phone country code *',
    options: '<div role="option">United States (+1)</div><div role="option">Canada (+1)</div><div role="option">United States (+44)</div>',
    extra: `<script>${selectionHandler('phone-country')}</script>`,
  }) + '<label>Phone<input id="phone" type="tel" autocomplete="tel-national"></label>', async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), 'United States (+1)');
    assert.equal(await page.locator('#phone').inputValue(), '512 555 0100');
    assert.ok(result.filled.some(field => field.key === 'phone_country_code'));
    assert.equal(result.failed.length, 0);
  });
});

test('a generic Country control in the shared phone container rejects a wrong prefix', async () => {
  await fixture(`<div class="phone-input">${selectControl({
    id: 'country', label: 'Country *',
    options: '<div role="option">United States +44</div><div role="option">Canada +1</div>',
  })}<label>Phone<input id="phone" type="tel"></label></div>
    <script>${selectionHandler('country')}</script>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('.select__single-value').textContent(), '');
    assert.equal(result.filled.some(field => field.key === 'phone_country_code'), false);
    assert.equal(result.filled.some(field => field.key === 'country'), false);
  });
});

test('duplicate code-only options remain ambiguous and unselected', async () => {
  await fixture(nativeSplitPhone('<option value="us">+1</option><option value="ca">+1</option>'), async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#country-code').inputValue(), '');
    assert.equal(result.filled.some(field => field.key === 'phone_country_code'), false);
    assert.ok(result.failed.some(field => /country code/i.test(field.label)));
  });
});

test('phone formatter equivalence accepts identical digits but rejects changed digits and extensions', async () => {
  for (const [actual, accepted] of [['(555) 010-1200', true], ['(555) 010-1201', false], ['(555) 010-1200 ext 9', false]]) {
    await fixture(`<label>Phone<input id="phone" type="tel"></label><script>
      document.querySelector('#phone').addEventListener('input', event => { event.target.value = ${JSON.stringify(actual)}; });
    </script>`, async page => {
      const result = await execute(page, 'details', { phone: '5550101200' });
      assert.equal(await page.locator('#phone').inputValue(), actual);
      assert.equal(result.filled.some(field => field.key === 'phone'), accepted, actual);
      assert.equal(result.failed.some(field => /phone/i.test(field.label)), !accepted, actual);
    });
  }
});

test('clearing an already filled connected phone field is preserved during a delayed lookup', async () => {
  await fixture(`<label>Phone<input id="phone" type="tel"></label>${selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    extra: `<script>${selectionHandler('candidate-location')}
      document.querySelector('#candidate-location').addEventListener('input', () => {
        setTimeout(() => {
          document.querySelector('#react-select-candidate-location-listbox').innerHTML = '<div role="option">Austin, Texas, United States</div>';
        }, 1000);
      });
    </script>`,
  })}`, async page => {
    const run = execute(page);
    await page.waitForFunction(() => document.querySelector('#phone').value !== '');
    await page.locator('#phone').fill('');
    const result = await run;
    assert.equal(await page.locator('#phone').inputValue(), '');
    assert.ok(result.failed.some(field => /phone/i.test(field.label)));
  });
});

test('same-URL step replacement interrupts autofill without touching the later step', async () => {
  await fixture(`<form id="step-one">${selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    extra: `<script>${selectionHandler('candidate-location')}
      document.querySelector('#candidate-location').addEventListener('input', () => {
        setTimeout(() => {
          const replacement = document.createElement('form');
          replacement.id = 'step-two';
          replacement.innerHTML = '<label>Last name<input id="later-last-name"></label>';
          document.querySelector('#step-one').replaceWith(replacement);
        }, 400);
      });
    </script>`,
  })}</form>`, async page => {
    const result = await execute(page);
    assert.equal(result.status, 'interrupted');
    assert.equal(await page.locator('#later-last-name').inputValue(), '');
    assert.ok(result.warnings.some(message => /step changed/i.test(message)));
  });
});

test('inert and aria-hidden inactive steps are not inventoried or filled', async () => {
  await fixture(`<form><label>First name<input id="visible-first-name"></label>
    <div aria-hidden="true"><label>Phone<input id="hidden-phone" type="tel"></label></div>
    <div inert><label>Last name<input id="inert-last-name"></label></div></form>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#visible-first-name').inputValue(), 'Alex');
    assert.equal(await page.locator('#hidden-phone').inputValue(), '');
    assert.equal(await page.locator('#inert-last-name').inputValue(), '');
    assert.equal(result.filled.length, 1);
  });
});

test('an ordinary Country and Phone contact fieldset retains residence country and full phone behavior', async () => {
  await fixture(`<fieldset><legend>Contact</legend>
    <label>Country<select id="country"><option value="">Choose</option><option value="us">United States</option></select></label>
    <label>Phone<input id="phone" type="tel"></label></fieldset>`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#country').inputValue(), 'us');
    assert.equal(await page.locator('#phone').inputValue(), '+1 512 555 0100');
    assert.ok(result.filled.some(field => field.key === 'country'));
    assert.ok(result.filled.some(field => field.key === 'phone'));
    assert.equal(result.failed.length, 0);
  });
});

test('replacement of one application form is detected even when another form remains', async () => {
  await fixture(`<form id="retained"><label>First name<input id="retained-first-name" value="Manual"></label></form>
    <form id="application-step">${selectControl({
      id: 'candidate-location', label: 'Location (City) *',
      extra: `<script>${selectionHandler('candidate-location')}
        document.querySelector('#candidate-location').addEventListener('input', () => {
          setTimeout(() => {
            const replacement = document.createElement('form');
            replacement.id = 'later-step';
            replacement.innerHTML = '<label>Last name<input id="two-form-later-last-name"></label>';
            document.querySelector('#application-step').replaceWith(replacement);
          }, 400);
        });
      </script>`,
    })}</form>`, async page => {
    const result = await execute(page);
    assert.equal(result.status, 'interrupted');
    assert.equal(await page.locator('#two-form-later-last-name').inputValue(), '');
    assert.ok(result.warnings.some(message => /step changed/i.test(message)));
  });
});

test('exact retained phone extensions or letters still fail the ordinary-phone contract', async () => {
  for (const phone of ['555-010-1200 ext 9', 'not a phone']) {
    await fixture('<label>Phone<input id="phone" type="tel"></label>', async page => {
      const result = await execute(page, 'details', { phone });
      assert.equal(await page.locator('#phone').inputValue(), phone);
      assert.equal(result.filled.some(field => field.key === 'phone'), false);
      assert.ok(result.failed.some(field => /phone/i.test(field.label)));
    });
  }
});

test('a successfully filled phone can retry after positive replacement evidence', async () => {
  await fixture(`<label>Phone<input id="phone" type="tel"></label>${selectControl({
    id: 'candidate-location', label: 'Location (City) *',
    extra: `<script>${selectionHandler('candidate-location')}
      document.querySelector('#phone').addEventListener('input', () => {
        setTimeout(() => {
          const original = document.querySelector('#phone');
          const replacement = original.cloneNode();
          replacement.value = '';
          original.replaceWith(replacement);
        }, 200);
      }, { once: true });
      document.querySelector('#candidate-location').addEventListener('input', () => {
        setTimeout(() => {
          document.querySelector('#react-select-candidate-location-listbox').innerHTML = '<div role="option">Austin, Texas, United States</div>';
        }, 1000);
      });
    </script>`,
  })}`, async page => {
    const result = await execute(page);
    assert.equal(await page.locator('#phone').inputValue(), '+1 512 555 0100');
    assert.ok(result.filled.some(field => field.key === 'phone'));
  });
});
