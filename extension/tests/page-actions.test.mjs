import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before, after, test} from 'node:test';
import {chromium} from '@playwright/test';
import {captureCurrentJob, runCurrentAction, SUPPORTED_HOSTS} from '../actions.js';

// Real DOMs in a disposable Chromium context. Every navigation is intercepted;
// the Chrome scripting bridge is modeled without an installed extension/profile.
const content = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const profile = {first_name: 'Alex', last_name: 'Example', email: 'alex@example.test'};
const resume = {metadata: {name: 'Exact.pdf'}, dataUrl: 'data:application/pdf;base64,JVBERi0xLjQK'};
const greenhouse = 'https://boards.greenhouse.io/example/jobs/123';
const basicForm = `<main><h1>Research engineer</h1><form id="application-form">
  <label>First name<input id="first_name" required></label>
  <label>Email<input id="email" value="existing@example.test" required></label>
  <label>Gender<input id="gender"></label><label><input id="consent" type="checkbox">I agree</label>
  <label>Why this company?<textarea id="why" required></textarea></label>
  <div class="field"><label>Résumé<input id="resume" name="resume" type="file"></label><span id="attachment"></span></div>
  <button type="submit">Submit application</button></form></main>
  <script>document.querySelector('form').onsubmit = e => { e.preventDefault(); document.body.dataset.submitted = 'true'; };
  document.querySelector('#resume').onchange = e => { document.querySelector('#attachment').textContent = e.target.files[0]?.name || ''; };</script>`;
const posting = `<script type="application/ld+json">${JSON.stringify({'@type': 'JobPosting', title: 'Research engineer', hiringOrganization: {name: 'Example'}, description: '<p>Build helpful tools.</p>'})}</script>`;
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function fixture({url = greenhouse, html = basicForm + posting, bodyForUrl, beforeScript, afterScript, overrideResult} = {}, run) {
  const context = await browser.newContext(), calls = [], documents = new Map(), frameIds = new Map();
  const page = await context.newPage();
  let active = page, nextFrame = 1, nextDocument = 1;
  context.on('page', p => watch(p));
  function watch(p) { p.on('framenavigated', frame => documents.set(frame, `document-${nextDocument++}`)); }
  watch(page);
  await context.route('**/*', route => route.request().isNavigationRequest()
    ? route.fulfill({contentType: 'text/html', body: bodyForUrl?.(new URL(route.request().url())) ?? html}) : route.abort());
  await page.goto(url);
  const idFor = frame => { if (frame === page.mainFrame()) return 0; if (!frameIds.has(frame)) frameIds.set(frame, nextFrame++); return frameIds.get(frame); };
  const bridge = {
    tabs: {query: async () => active ? [{id: active === page ? 7 : 8, url: active.url(), title: await active.title()}] : []},
    scripting: {executeScript: async request => {
      calls.push(request);
      await beforeScript?.(request, {page, context, setActive: value => { active = value; }});
      let frames = request.target.allFrames ? page.frames() : request.target.documentIds
        ? page.frames().filter(frame => request.target.documentIds.includes(documents.get(frame)))
        : request.target.frameIds ? page.frames().filter(frame => request.target.frameIds.includes(idFor(frame))) : [page.mainFrame()];
      // CAPTCHA/ad frames have no host grant and return no injection result.
      frames = frames.filter(frame => !frame.url().includes('inaccessible.test'));
      if (!frames.length) throw new Error('No document with the requested id');
      const results = [];
      for (const frame of frames) {
        if (request.files) await frame.addScriptTag({content});
        const result = request.func ? await frame.evaluate(({source, args}) => (0, eval)(`(${source})`)(...args), {source: request.func.toString(), args: request.args || []}) : undefined;
        results.push({frameId: idFor(frame), documentId: documents.get(frame), ...(request.func ? {result} : {})});
      }
      await afterScript?.(request, {page, context, setActive: value => { active = value; }});
      return overrideResult ? overrideResult(request, results) : results;
    }},
  };
  globalThis.chrome = bridge;
  try { return await run({page, context, calls, setActive: value => { active = value; }}); }
  finally { await context.close(); }
}

test('capture, inspect, fill and attach work on all seven supported hosts without touching protected answers or submitting', async t => {
  for (const host of SUPPORTED_HOSTS) await t.test(host, async () => {
    const path = host.includes('greenhouse') ? '/example/jobs/123' : '/example/req-123/application';
    await fixture({url: `https://${host}${path}`}, async ({page}) => {
      const captured = await captureCurrentJob();
      assert.equal(captured.company, 'Example'); assert.equal(captured.title, 'Research engineer');
      assert.equal(captured.url, page.url()); assert.equal(captured.supported, true);
      const inspection = await runCurrentAction('inspect', profile, resume, {expectedUrl: captured.url});
      assert.equal(inspection.status, 'complete');
      assert.ok(inspection.frames[0].required.some(field => field.label === 'First name'));
      assert.equal(await page.locator('#first_name').inputValue(), '');
      const fill = await runCurrentAction('details', profile, resume, {expectedUrl: captured.url});
      assert.equal(fill.status, 'complete');
      assert.equal(await page.locator('#first_name').inputValue(), 'Alex');
      assert.equal(await page.locator('#email').inputValue(), 'existing@example.test');
      assert.equal(await page.locator('#gender').inputValue(), '');
      assert.equal(await page.locator('#consent').isChecked(), false);
      assert.equal(await page.locator('#why').inputValue(), '');
      assert.equal(await page.locator('#resume').evaluate(input => input.files.length), 0);
      const attached = await runCurrentAction('resume', profile, resume, {expectedUrl: captured.url});
      assert.equal(attached.frames[0].resume.status, 'accepted');
      assert.equal(await page.locator('#resume').evaluate(input => input.files[0].name), 'Exact.pdf');
      const again = await runCurrentAction('resume', profile, {...resume, metadata: {name: 'Other.pdf'}}, {expectedUrl: captured.url});
      assert.equal(again.frames[0].resume.status, 'already-attached');
      assert.equal(await page.locator('#resume').evaluate(input => input.files[0].name), 'Exact.pdf');
      assert.equal(await page.locator('body').getAttribute('data-submitted'), null);
    });
  });
});

test('capture rejects missing results instead of fabricating a captured job from tab.title', async () => {
  await fixture({overrideResult: () => []}, async () => {
    await assert.rejects(captureCurrentJob(), /capture|document|result/i);
  });
});

test('capture cannot combine metadata from a replacement document with the old job URL', async () => {
  let navigated = false;
  await fixture({beforeScript: async (request, {page}) => {
    if (!navigated && !request.files) { navigated = true; await page.goto(greenhouse.replace('/123', '/456')); }
  }}, async () => { await assert.rejects(captureCurrentJob(), /changed|navigat|current/i); });
});

test('switching the active tab during preflight cannot fill the previous tab', async () => {
  let switched = false;
  await fixture({afterScript: async (request, {context, setActive}) => {
    if (!switched && request.args?.[0]?.probe) {
      switched = true; const other = await context.newPage(); await other.goto(greenhouse.replace('/123', '/456')); setActive(other);
    }
  }}, async ({page}) => {
    const report = await runCurrentAction('details', profile, null, {expectedUrl: greenhouse});
    assert.equal(await page.locator('#first_name').inputValue(), '', 'the previous page must not receive the profile');
    assert.equal(report.status, 'interrupted');
    assert.match(JSON.stringify(report), /tab|changed|switch/i);
  });
});

test('no-form pages and empty action returns do not count as successful inspection', async () => {
  await fixture({html: '<main><h1>Jobs closed</h1><p>This role is no longer accepting applications.</p></main>'}, async () => {
    const report = await runCurrentAction('inspect', {});
    assert.equal(report.status, 'interrupted');
    assert.match(JSON.stringify(report), /form|application/i);
  });
  await fixture({overrideResult: (request, results) => request.args?.[0]?.action ? [{...results[0], result: {}}] : results}, async () => {
    const report = await runCurrentAction('inspect', {});
    assert.equal(report.status, 'interrupted');
  });
});

test('unsupported, missing and privileged tabs fail before autofill and manual capture stays available', async () => {
  await fixture({url: 'https://employer.test/jobs/123'}, async ({calls, setActive}) => {
    const capture = await captureCurrentJob(); assert.equal(capture.supported, false);
    for (const action of ['inspect', 'details', 'resume']) await assert.rejects(runCurrentAction(action, profile, resume), /supports|host/i);
    assert.equal(calls.length, 1);
    setActive(null);
    await assert.rejects(captureCurrentJob(), /job page|webpage/i);
  });
  const calls = [];
  for (const url of ['chrome://extensions/', 'file:///tmp/fixture.html', 'chrome-extension://example/page.html', 'about:blank']) {
    globalThis.chrome = {tabs: {query: async () => [{id: 1, url}]}, scripting: {executeScript: async request => calls.push(request)}};
    await assert.rejects(captureCurrentJob(), /webpage/i);
    await assert.rejects(runCurrentAction('inspect', {}), /webpage/i);
  }
  assert.equal(calls.length, 0);
});

test('embedded forms, inaccessible third-party frames and hidden matching forms receive the intended scope', async t => {
  for (const hidden of [false, true]) await t.test(hidden ? 'hidden form stays unchanged' : 'one visible embedded form fills', async () => {
    await fixture({bodyForUrl: url => url.hostname === 'inaccessible.test' ? '<p>CAPTCHA</p>' : url.searchParams.has('embedded') ? basicForm
      : `<main><h1>Research engineer</h1><iframe ${hidden ? 'style="display:none"' : ''} src="${greenhouse}?embedded=1"></iframe><iframe src="https://inaccessible.test/captcha"></iframe></main>`}, async ({page, calls}) => {
      await page.waitForLoadState('load');
      const embedded = page.frames().find(frame => frame.url().includes('embedded=1'));
      const report = await runCurrentAction('details', profile, null, {expectedUrl: greenhouse});
      assert.equal(await embedded.locator('#first_name').inputValue(), hidden ? '' : 'Alex', JSON.stringify(report));
      assert.equal(report.status, hidden ? 'interrupted' : 'complete');
      const privateCalls = calls.filter(request => request.args?.[0]?.profile?.first_name);
      assert.equal(privateCalls.length, hidden ? 0 : 1);
    });
  });
});

test('pending navigation is rejected before any page injection', async () => {
  const calls = [];
  globalThis.chrome = {tabs: {query: async () => [{id: 7, url: greenhouse, pendingUrl: greenhouse.replace('/123', '/456'), status: 'loading'}]},
    scripting: {executeScript: async request => { calls.push(request); return []; }}};
  await assert.rejects(captureCurrentJob(), /load|navigat|wait/i);
  assert.equal(calls.length, 0);
});

test('HTTP variants of every supported host remain capture-only', async t => {
  for (const host of SUPPORTED_HOSTS) await t.test(host, async () => {
    await fixture({url: `http://${host}/example/jobs/123`}, async ({calls}) => {
      assert.equal((await captureCurrentJob()).supported, false);
      for (const action of ['inspect', 'details', 'resume']) await assert.rejects(runCurrentAction(action, profile, resume), /supports|host/i);
      assert.equal(calls.length, 1, 'only public metadata capture runs');
    });
  });
});

test('custom employer and lookalike pages support manual capture without autofill', async t => {
  for (const url of ['https://careers.employer.test/role/1', 'https://example.wd5.myworkdayjobs.com.evil.test/en-US/careers/job/1', 'https://jobs.ashbyhq.com.evil.test/example/123']) await t.test(new URL(url).hostname, async () => {
    await fixture({url}, async ({calls}) => {
      assert.equal((await captureCurrentJob()).provider, 'Manual');
      for (const action of ['inspect', 'details', 'resume']) await assert.rejects(runCurrentAction(action, profile, resume), /supports|host/i);
      assert.equal(calls.length, 1);
    });
  });
});

test('PDF URLs and non-HTML capture results fail clearly without capturing a fake job', async () => {
  await fixture({url: 'https://boards.greenhouse.io/attachments/resume.pdf'}, async ({calls}) => {
    for (const action of ['inspect', 'details', 'resume']) await assert.rejects(runCurrentAction(action, profile, resume), /PDF/);
    await assert.rejects(captureCurrentJob(), /PDF/); assert.equal(calls.length, 0);
  });
  await fixture({overrideResult: (request, results) => !request.args && !request.files ? results.map(item => ({...item, result: {...item.result, documentType: 'application/pdf'}})) : results}, async () => {
    await assert.rejects(captureCurrentJob(), /PDF|file/);
  });
});

test('capture tolerates malformed or absent metadata and never picks a random job from a listing', async t => {
  const fixtures = [
    ['wrong metadata types', {'@type': 'JobPosting', title: {bad: 'title'}, description: {bad: 'description'}, hiringOrganization: {name: {bad: 'company'}}, identifier: {value: {bad: 'id'}}}],
    ['multiple postings', [{'@type': 'JobPosting', title: 'Wrong first job'}, {'@type': 'JobPosting', title: 'Wrong second job'}]],
    ['no metadata', null],
  ];
  for (const [name, metadata] of fixtures) await t.test(name, async () => {
    await fixture({html: `<title>Open roles</title><main><h1>Open roles</h1><p>Choose a role.</p></main>${metadata ? `<script type="application/ld+json">${JSON.stringify(metadata)}</script>` : ''}`}, async () => {
      const capture = await captureCurrentJob(); assert.equal(capture.title, 'Open roles');
      assert.equal(capture.company, ''); assert.equal(capture.request_id, '');
      assert.match(capture.description, /Choose a role/);
      assert.equal((await runCurrentAction('inspect', {})).status, 'interrupted');
    });
  });
});

test('newsletter semantic forms, headers and footers are not application targets', async t => {
  for (const attributes of ['aria-label="Newsletter"', 'aria-labelledby="newsletter-title"', '']) await t.test(attributes || 'direct heading', async () => {
    await fixture({url: 'https://jobs.ashbyhq.com/example/req-123', html: `<header><label>First name<input id="header-name" name="first_name"></label></header>
      <main><h1>Open jobs</h1><form ${attributes}><h2 id="newsletter-title">Subscribe for company news</h2><label>Email<input id="newsletter-email" type="email"></label></form></main>
      <footer><label>Email<input id="footer-email" name="email"></label></footer>`}, async ({page, calls}) => {
      const report = await runCurrentAction('details', profile, null);
      assert.equal(report.status, 'interrupted');
      for (const id of ['header-name', 'newsletter-email', 'footer-email']) assert.equal(await page.locator(`#${id}`).inputValue(), '');
      assert.ok(!calls.some(request => request.args?.[0]?.profile?.email), 'no private payload reaches unrelated forms');
    });
  });
});

test('ambiguous visible frames are inspected publicly and expired page grants do not replay a mutation', async () => {
  await fixture({bodyForUrl: url => url.searchParams.has('embedded') ? basicForm : `${basicForm}<iframe src="${greenhouse}?embedded=1"></iframe>`}, async ({page, calls}) => {
    const report = await runCurrentAction('details', profile);
    assert.equal(report.status, 'interrupted'); assert.match(JSON.stringify(report.warnings), /Several visible/);
    for (const frame of page.frames()) assert.equal(await frame.locator('#first_name').inputValue(), '');
    assert.ok(!calls.some(request => request.args?.[0]?.profile?.first_name));
  });
  const calls = [];
  globalThis.chrome = {tabs: {query: async () => [{id: 7, url: greenhouse}]}, scripting: {executeScript: async request => { calls.push(request); throw new Error('Cannot access contents of url. Extension manifest must request permission to access this host.'); }}};
  const report = await runCurrentAction('details', profile);
  assert.equal(report.status, 'interrupted'); assert.equal(calls.length, 1);
  assert.match(JSON.stringify(report), /JobPilot icon|inspect the current page/);
  await assert.rejects(captureCurrentJob(), /access expired|toolbar icon/);
});

test('a research application form is a valid target rather than a search form', async () => {
  await fixture({html: basicForm.replace('id="application-form"', 'id="research-application"')}, async ({page}) => {
    const report = await runCurrentAction('details', profile, null, {expectedUrl: greenhouse});
    assert.equal(report.status, 'complete');
    assert.equal(await page.locator('#first_name').inputValue(), 'Alex');
    const attached = await runCurrentAction('resume', profile, resume, {expectedUrl: greenhouse});
    assert.equal(attached.frames[0].resume.status, 'accepted');
  });
});
