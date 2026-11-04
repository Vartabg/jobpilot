import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before, after, test} from 'node:test';
import {chromium} from '@playwright/test';

const content = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const startUrl = 'https://boards.greenhouse.io/example/jobs/123';
const profile = {first_name: 'Alex', last_name: 'Example', email: 'alex@example.test', country: 'United States'};
const resume = {metadata: {name: 'Exact.pdf'}, dataUrl: 'data:application/pdf;base64,JVBERi0xLjQK'};
const delayedCombo = `<form id="application-form"><label>First name<input id="first_name"></label>
  <div class="field"><label>Country<input id="country" role="combobox" aria-controls="countries" aria-expanded="true"></label>
  <div id="countries" role="listbox"></div></div><label>Last name<input id="last_name"></label></form>
  <script>window.optionClicks=0; window.cleanupEvents=0;
  document.querySelector('#country').addEventListener('keydown',()=>window.cleanupEvents++);
  function addOption(){document.querySelector('#countries').innerHTML='<div role="option">United States</div>';
    document.querySelector('[role=option]').addEventListener('click',()=>window.optionClicks++);}
  </script>`;
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });
async function fixture(html, run) {
  const context = await browser.newContext(), page = await context.newPage();
  await context.route('**/*', route => route.request().isNavigationRequest() ? route.fulfill({contentType: 'text/html', body: html}) : route.abort());
  await page.goto(startUrl); await page.addScriptTag({content});
  try { return await run(page); } finally { await context.close(); }
}
const execute = (page, action = 'details') => page.evaluate(payload => __jobpilotRun(payload), {action, profile, resume, requireVisible: true});

test('SPA navigation during a delayed dropdown stops before option clicks and retains verified earlier fills', async () => {
  await fixture(delayedCombo, async page => {
    const running = execute(page);
    await page.waitForFunction(() => document.querySelector('#country').value === 'United States');
    await page.evaluate(() => { history.pushState(null, '', '/example/jobs/456'); addOption(); });
    const report = await running;
    assert.equal(report.status, 'interrupted');
    assert.equal(await page.evaluate(() => optionClicks), 0);
    assert.equal(await page.locator('#last_name').inputValue(), '');
    assert.equal(await page.locator('#country').inputValue(), 'United States', 'do not clean up a different page');
    assert.equal(await page.evaluate(() => cleanupEvents), 0);
    assert.ok(report.filled.some(field => field.key === 'first_name'), 'already verified progress stays in the report');
  });
});

test('a hidden-then-visible event cancels a pending dropdown permanently instead of resuming on return', async () => {
  await fixture(delayedCombo, async page => {
    // Headless Chromium keeps all tabs visible. Model the real browser's
    // visibility events in its DOM while retaining real timers and controls.
    await page.evaluate(() => { window.fixtureVisibility = 'visible'; Object.defineProperty(document, 'visibilityState', {get: () => window.fixtureVisibility}); });
    const running = execute(page);
    await page.waitForFunction(() => document.querySelector('#country').value === 'United States');
    await page.evaluate(() => {
      window.fixtureVisibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange'));
      window.fixtureVisibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')); addOption();
    });
    const report = await running;
    assert.equal(report.status, 'interrupted'); assert.equal(await page.evaluate(() => optionClicks), 0);
    assert.equal(await page.evaluate(() => cleanupEvents), 0);
    assert.equal(await page.locator('#last_name').inputValue(), '');
  });
});

test('pagehide cancels even when the old document still reports its original visible URL', async () => {
  await fixture(delayedCombo, async page => {
    const running = execute(page);
    await page.waitForFunction(() => document.querySelector('#country').value === 'United States');
    await page.evaluate(() => { dispatchEvent(new Event('pagehide')); addOption(); });
    const report = await running;
    assert.equal(report.status, 'interrupted'); assert.equal(await page.evaluate(() => optionClicks), 0);
  });
});

test('navigation from an option mousedown prevents subsequent mouseup and click', async () => {
  await fixture(delayedCombo, async page => {
    await page.evaluate(() => {
      addOption(); window.mouseUps=0;
      document.querySelector('[role=option]').addEventListener('mousedown', () => history.pushState(null, '', '/example/jobs/456'));
      document.querySelector('[role=option]').addEventListener('mouseup', () => window.mouseUps++);
    });
    const report = await execute(page);
    assert.equal(report.status, 'interrupted'); assert.equal(await page.evaluate(() => optionClicks), 0);
    assert.equal(await page.evaluate(() => mouseUps), 0);
  });
});

test('navigation during upload polling preserves the selected file but cannot report acceptance', async () => {
  await fixture(`<form id="application-form"><div class="field"><label>Resume<input id="resume" type="file" name="resume"></label><span id="filename"></span></div></form>
    <script>document.querySelector('#resume').onchange=e=>{setTimeout(()=>history.pushState(null,'','/example/jobs/456'),20);
      setTimeout(()=>document.querySelector('#filename').textContent=e.target.files[0].name,50);};</script>`, async page => {
    const report = await execute(page, 'resume');
    assert.equal(report.status, 'interrupted'); assert.equal(report.resume.status, 'prepared');
    assert.equal(await page.locator('#resume').evaluate(input => input.files[0].name), 'Exact.pdf');
    assert.match(report.resume.message, /could not be verified/);
  });
});

test('a visible display:contents application and its resume work alongside hidden obsolete and disabled upload inputs', async () => {
  await fixture(`<form id="old-application" style="display:none"><label>First name<input id="old-name"></label><label>Resume<input id="old-resume" type="file"></label></form>
    <form id="application-form" style="display:contents"><label>First name<input id="first_name"></label>
      <div class="field"><label>Resume<input id="disabled-resume" type="file" disabled></label></div>
      <div class="field"><label>Resume<input id="resume" type="file"></label><span id="filename"></span></div></form>
    <script>document.querySelector('#resume').onchange=e=>document.querySelector('#filename').textContent=e.target.files[0].name;</script>`, async page => {
    const fill = await execute(page); assert.equal(fill.status, 'complete');
    assert.equal(await page.locator('#first_name').inputValue(), 'Alex'); assert.equal(await page.locator('#old-name').inputValue(), '');
    const upload = await execute(page, 'resume'); assert.equal(upload.resume.status, 'accepted');
    assert.equal(await page.locator('#resume').evaluate(input => input.files[0].name), 'Exact.pdf');
    for (const id of ['old-resume', 'disabled-resume']) assert.equal(await page.locator(`#${id}`).evaluate(input => input.files.length), 0);
  });
});

test('header, footer and newsletter fields stay unchanged while a recognized application fills', async () => {
  await fixture(`<header><label>First name<input id="header-name" name="first_name"></label></header>
    <main><form aria-label="Newsletter"><h2>Subscribe for news</h2><label>Email<input id="newsletter-email" type="email"></label></form>
    <form id="application-form"><label>First name<input id="first_name"></label><label>Email<input id="email" type="email"></label></form></main>
    <footer><label>Email<input id="footer-email" name="email"></label></footer>`, async page => {
    const report = await execute(page); assert.equal(report.status, 'complete');
    assert.equal(await page.locator('#first_name').inputValue(), 'Alex'); assert.equal(await page.locator('#email').inputValue(), 'alex@example.test');
    for (const id of ['header-name', 'newsletter-email', 'footer-email']) assert.equal(await page.locator(`#${id}`).inputValue(), '');
  });
});

test('multiple visible application forms block mutation and disabled-only upload fields stay empty', async () => {
  await fixture(`<form id="application-one"><label>First name<input id="first_name"></label></form>
    <form id="application-two"><label>Last name<input id="last_name"></label></form>`, async page => {
    assert.equal((await execute(page)).status, 'interrupted');
    assert.equal(await page.locator('#first_name').inputValue(), ''); assert.equal(await page.locator('#last_name').inputValue(), '');
    const inspection = await execute(page, 'inspect'); assert.equal(inspection.action, 'inspect');
  });
  await fixture('<form id="application-form"><div class="field"><label>Resume<input id="resume" type="file" disabled></label></div></form>', async page => {
    const report = await execute(page, 'resume');
    assert.equal(report.resume.status, 'no-target'); assert.equal(report.status, 'partial');
    assert.equal(await page.locator('#resume').evaluate(input => input.files.length), 0);
  });
});
