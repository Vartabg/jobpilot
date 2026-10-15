import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir, mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {before, after, test} from 'node:test';
import {chromium, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const root = new URL('../', import.meta.url);
const screenshotDir = process.env.JOBPILOT_WORKSPACE_SCREENSHOTS || '/tmp/jobpilot-career-review/screenshots';
const pdf = Buffer.from('%PDF-1.4\nsynthetic frozen resume\n%%EOF');
const job = {company: 'Fixture Co', title: 'Customer engineer', url: 'https://jobs.ashbyhq.com/fixture/req-1'};
const account = {id: 'field-story', title: 'Customer site repair', summary: 'Diagnosed a recurring device fault for a client.',
  details: ['I isolated the failed component.'], skills: ['diagnostics'], question_fit: ['difficult customer'],
  truth_boundaries: ['Do not invent a team size.'], source: 'Synthetic test account', reviewed: true};
let browser, server, base;
before(async () => {
  server = createServer(async (req, res) => {
    const name = new URL(req.url, 'http://localhost').pathname.slice(1);
    if (!/^[a-z0-9.-]+$/.test(name)) return res.writeHead(404).end();
    try { const body = await readFile(new URL(name, root)); res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html'); res.end(body); }
    catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch();
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function workspace(run, viewport = {width: 1200, height: 900}) {
  const context = await browser.newContext({viewport, reducedMotion: 'reduce'}), page = await context.newPage(), errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => { const url = route.request().url(); if (url.startsWith(base)) return route.continue(); external.push(url); return route.abort(); });
  await page.addInitScript(() => {
    const values = JSON.parse(localStorage.getItem('__fixtureChrome') || '{}'); let queue = Promise.resolve();
    const event = {addListener() {}, removeListener() {}};
    globalThis.chrome = {runtime: {getURL: path => new URL(path, location.href).href, getManifest: () => ({version: 'test'}), onMessage: event,
      sendMessage: message => {
        const response = queue.then(async () => {
          try { const {executeModelOperation} = await import('./model.js'); return {ok: true, result: await executeModelOperation(message.operation, message.args)}; }
          catch (error) { return {ok: false, error: error.message}; }
        }); queue = response.then(() => {}, () => {}); return response;
      }}, storage: {onChanged: event, local: {
      get: async key => key == null ? structuredClone(values) : Object.fromEntries([key].flat().map(name => [name, structuredClone(values[name])])),
      set: async update => { Object.assign(values, structuredClone(update)); localStorage.setItem('__fixtureChrome', JSON.stringify(values)); },
      remove: async keys => { for (const key of [keys].flat()) delete values[key]; localStorage.setItem('__fixtureChrome', JSON.stringify(values)); },
    }}};
  });
  await page.goto(`${base}/workspace.html`); await expect(page.getByRole('heading', {name: 'Story Vault', exact: true})).toBeVisible();
  try { await run(page); assert.deepEqual(errors, [], 'workspace browser errors'); assert.deepEqual(external, [], 'no external requests'); }
  finally { await context.close(); }
}
const rpc = (page, operation, ...args) => page.evaluate(async ({operation, args}) => {
  const response = await chrome.runtime.sendMessage({type: 'jobpilot:model', operation, args});
  if (!response.ok) throw new Error(response.error); return response.result;
}, {operation, args});
const call = (page, operation, ...args) => rpc(page, `workspace:${operation}`, ...args);
async function downloaded(page, action) {
  const waiting = page.waitForEvent('download'); await action(); const download = await waiting;
  return {name: download.suggestedFilename(), bytes: await readFile(await download.path())};
}

test('career memory saves approved wording, finds synonyms, persists and exports safe source text', async () => {
  await workspace(async page => {
    const memory = page.locator('#memory');
    await memory.getByLabel('Story title', {exact: true}).fill(account.title);
    await memory.getByLabel('What happened and your contribution').fill(account.summary);
    await memory.getByLabel('Skills — one per line').fill('diagnostics');
    await memory.getByLabel('Truth boundaries — one per line').fill(account.truth_boundaries[0]);
    await memory.getByLabel('Source or reference').fill('<img src=x onerror=alert(1)>');
    await memory.getByLabel('I reviewed this wording').check(); await memory.getByRole('button', {name: 'Save story', exact: true}).click();
    await expect(memory.locator('article')).toContainText('Wording reviewed');
    await memory.getByLabel('Question or role context').fill('troubleshooting for customers');
    await expect(memory.locator('article')).toContainText('Matches: troubleshooting, customers');
    assert.equal(await memory.locator('article img').count(), 0);
    const exported = await downloaded(page, () => memory.getByRole('button', {name: 'Export matching stories as JSON'}).click());
    const packet = JSON.parse(exported.bytes.toString()); assert.equal(packet.stories.length, 1); assert.ok(packet.stories[0].reviewedAt);
    assert.deepEqual(packet.stories[0].truth_boundaries, account.truth_boundaries);
    await page.reload(); await expect(page.locator('#memory article')).toContainText(account.summary);
    await memory.getByRole('button', {name: 'Edit story', exact: true}).click();
    await memory.getByLabel('What happened and your contribution').fill('Corrected contribution.');
    await expect(memory.getByLabel('I reviewed this wording')).not.toBeChecked();
    await memory.getByRole('button', {name: 'Save changes'}).click();
    await expect(memory.locator('article')).toContainText('Needs wording review · revision 2');
  });
});

test('inbox stages previews without tracker writes, reconciles conflicts, preserves notes and dedupes', async () => {
  await workspace(async page => {
    const existing = await rpc(page, 'saveApplication', {...job, notes: 'Local interview note'}, 'interview');
    await page.getByRole('tab', {name: 'Unified inbox'}).click(); const inbox = page.locator('#inbox');
    const input = JSON.stringify([{...job, status: 'rejected', notes: 'Imported decision'}]);
    await inbox.getByLabel('Or paste job records').fill(input); await inbox.getByRole('button', {name: 'Preview pasted records'}).click();
    await expect(inbox.getByText('Review import: 1 job records')).toBeVisible();
    assert.equal((await rpc(page, 'listApplications'))[0].status, 'interview'); assert.equal((await call(page, 'getInbox')).entries.length, 0);
    await inbox.getByRole('button', {name: 'Stage records for review'}).click();
    await expect(inbox.getByText('Imported status: rejected', {exact: false})).toBeVisible();
    await inbox.getByRole('button', {name: 'Keep current record'}).click();
    assert.equal((await rpc(page, 'listApplications'))[0].status, 'interview');
    await inbox.getByLabel('Or paste job records').fill(input); await inbox.getByRole('button', {name: 'Preview pasted records'}).click();
    await inbox.getByRole('button', {name: 'Stage records for review'}).click();
    await expect(page.locator('#workspace-notice')).toContainText('0 items staged; 1 repeated records skipped');
    await call(page, 'stageImport', [{...job, status: 'rejected', notes: 'Later source row'}], 'New source');
    await inbox.getByRole('button', {name: 'Refresh inbox and tracker'}).click();
    await inbox.getByRole('button', {name: 'Use imported update'}).click();
    const [saved] = await rpc(page, 'listApplications'); assert.equal(saved.id, existing.id); assert.equal(saved.status, 'rejected');
    assert.match(saved.notes, /Local interview note/); assert.match(saved.notes, /Later source row/);
  });
});

test('an inbox review rejects a stale tracker comparison and changes no decision', async () => {
  await workspace(async page => {
    const existing = await rpc(page, 'saveApplication', job, 'started');
    await call(page, 'stageImport', [{...job, status: 'submitted'}], 'Synthetic CSV');
    await page.getByRole('tab', {name: 'Unified inbox'}).click();
    await expect(page.getByRole('button', {name: 'Use imported update'})).toBeVisible();
    await rpc(page, 'updateApplication', existing.id, {status: 'interview'});
    await page.getByRole('button', {name: 'Use imported update'}).click();
    await expect(page.locator('#workspace-notice')).toContainText('changed while you reviewed it');
    assert.equal((await rpc(page, 'listApplications'))[0].status, 'interview');
    assert.equal((await call(page, 'getInbox')).entries[0].state, 'pending');
  });
});

test('stale story edits and deletions cannot overwrite newer wording or resurrect removed accounts', async () => {
  await workspace(async page => {
    const first = await call(page, 'saveStory', account); await page.reload(); const memory = page.locator('#memory');
    await memory.getByRole('button', {name: 'Edit story', exact: true}).click();
    const latest = await call(page, 'saveStory', {...first, summary: 'Newer wording saved by another workspace.'});
    await memory.getByLabel('What happened and your contribution').fill('Stale draft in the first workspace.');
    await memory.getByRole('button', {name: 'Save changes'}).click();
    await expect(memory.getByRole('alert')).toContainText('changed in another workspace');
    assert.equal((await call(page, 'listStories'))[0].summary, latest.summary);
    await assert.rejects(call(page, 'deleteStory', first.id, first.revision), /changed|removed/i);
    await call(page, 'deleteStory', latest.id, latest.revision);
    await assert.rejects(call(page, 'saveStory', {...latest, summary: 'Resurrected stale draft'}), /removed/i);
    assert.deepEqual(await call(page, 'listStories'), []);
  });
});

test('a long import filename retains full provenance while the canonical tracker source remains bounded', async () => {
  await workspace(async page => {
    await page.getByRole('tab', {name: 'Unified inbox'}).click(); const inbox = page.locator('#inbox');
    const filename = `Synthetic-${'very-long-tracker-name-'.repeat(7)}.csv`;
    await inbox.getByLabel('Import a tracker or shortlist').setInputFiles({name: filename, mimeType: 'text/csv',
      buffer: Buffer.from(`Company,Role,URL,Status,Notes\nFixture Co,Customer engineer,${job.url},submitted,Source confirmation\n`)});
    await expect(inbox.getByText('Review import: 1 job records')).toBeVisible();
    await inbox.getByRole('button', {name: 'Stage records for review'}).click();
    await inbox.getByRole('button', {name: 'Add to tracker'}).click();
    const [saved] = await rpc(page, 'listApplications'); assert.equal(saved.status, 'submitted');
    assert.equal(saved.source, 'career-inbox'); assert.match(saved.notes, /Source confirmation/); assert.ok(saved.notes.includes(filename));
    const [entry] = (await call(page, 'getInbox')).entries;
    assert.equal(entry.source.label, filename); assert.equal(entry.state, 'reviewed'); assert.equal(entry.decision, 'accept');
  });
});

test('capsules require review, freeze exact files and answers, and survive deleting the original résumé', async () => {
  await workspace(async page => {
    const story = await call(page, 'saveStory', account);
    const resume = await rpc(page, 'addResume', {name: 'Original.pdf', size: pdf.length, type: 'application/pdf'}, `data:application/pdf;base64,${pdf.toString('base64')}`);
    await page.getByRole('tab', {name: 'Time capsules'}).click(); const capsules = page.locator('#capsules');
    await capsules.getByLabel('Company', {exact: true}).fill(job.company); await capsules.getByLabel('Role', {exact: true}).fill(job.title);
    await capsules.getByLabel('Job URL', {exact: true}).fill(job.url); await capsules.getByLabel('Saved résumé used').selectOption(resume.id);
    await capsules.getByLabel('Job description as it appeared').fill('  Exact posting\nsecond line.  ');
    await capsules.getByLabel('Customer site repair · revision 1').check();
    await capsules.getByRole('button', {name: 'Add a question and answer'}).click();
    await capsules.getByLabel('Question', {exact: true}).fill('  Your contribution?  ');
    const answer = '  I isolated the fault.\n\nThen explained it.  ';
    await capsules.getByLabel('Final answer', {exact: true}).fill(answer);
    await capsules.getByRole('button', {name: 'Review capsule', exact: true}).click();
    await capsules.getByRole('button', {name: 'Seal this application record'}).click();
    await expect(page.locator('#workspace-notice')).toContainText('Confirm your submission');
    assert.equal((await call(page, 'listCapsules')).length, 0);
    await capsules.getByLabel('I submitted this application').check(); await capsules.getByRole('button', {name: 'Seal this application record'}).click();
    await expect(capsules.getByRole('button', {name: 'Open saved materials'})).toBeVisible();
    const [summary] = await call(page, 'listCapsules'), sealed = await call(page, 'getCapsule', summary.id);
    assert.equal(sealed.answers[0].answer, answer); assert.equal(sealed.postingText, '  Exact posting\nsecond line.  ');
    await rpc(page, 'deleteResume', resume.id); await rpc(page, 'saveProfile', {first_name: 'Changed later'});
    await call(page, 'saveStory', {...story, summary: 'Changed later'});
    assert.deepEqual(await call(page, 'getCapsule', summary.id), sealed);
    await capsules.getByRole('button', {name: 'Open saved materials'}).click();
    const exact = await downloaded(page, () => capsules.getByRole('button', {name: 'Download exact résumé'}).click());
    assert.equal(exact.name, 'Original.pdf'); assert.deepEqual(exact.bytes, pdf);
  });
});

test('backup validation is atomic, repeat restore is idempotent and imported story approval requires review', async () => {
  await workspace(async page => {
    await call(page, 'saveStory', account); await call(page, 'stageStories', [account], 'Legacy true accounts');
    const backup = await call(page, 'exportWorkspace'), malformed = structuredClone(backup);
    const capsule = await page.evaluate(async application => {
      const {sealCapsule} = await import('./capsule.js');
      return sealCapsule({confirmed: true, application, submittedAt: '2026-10-15T14:30:00Z', postingText: 'Synthetic posting', answers: []});
    }, job);
    malformed.capsules.push(capsule);
    malformed.inbox.push({kind: 'opportunity', job: {...job, url: 'javascript:alert(1)', status: 'submitted'}, source: {label: 'Bad row'}});
    await assert.rejects(call(page, 'restoreWorkspace', malformed), /URL|http/i);
    assert.deepEqual(await call(page, 'exportWorkspace').then(value => ({...value, exportedAt: ''})), {...backup, exportedAt: ''});
    assert.equal((await call(page, 'listCapsules')).length, 0, 'valid earlier capsules cannot be written before a later invalid row is rejected');
    backup.capsules.push(capsule);
    const first = await call(page, 'restoreWorkspace', backup), firstCount = (await call(page, 'getInbox')).entries.length;
    assert.ok(first.pending > 0); assert.equal(first.capsules, 1);
    const second = await call(page, 'restoreWorkspace', backup);
    assert.equal(second.pending, 0); assert.equal(second.capsules, 0); assert.equal((await call(page, 'getInbox')).entries.length, firstCount);
    assert.deepEqual(await call(page, 'getCapsule', capsule.id), capsule);
    assert.ok((await call(page, 'getInbox')).entries.filter(entry => entry.state === 'pending').every(entry => !entry.story.reviewedAt));
  });
});

test('legacy story import previews and stages candidate accounts without trusting imported approvals', async () => {
  await workspace(async page => {
    const memory = page.locator('#memory');
    await memory.getByLabel('Import story JSON').setInputFiles({name: 'synthetic-accounts.json', mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({accounts: [{...account, reviewedAt: '2026-10-14T12:00:00Z'}]}))});
    await expect(memory.getByRole('heading', {name: 'Preview: 1 imported stories'})).toBeVisible();
    assert.equal((await call(page, 'listStories')).length, 0);
    await memory.getByRole('button', {name: 'Stage stories in Inbox'}).click();
    await page.getByRole('tab', {name: 'Unified inbox'}).click();
    await expect(page.locator('#inbox')).toContainText('Imported wording needs review');
    await page.getByRole('button', {name: 'Add to career memory'}).click();
    const [saved] = await call(page, 'listStories'); assert.equal(saved.reviewedAt, '');
    await page.getByRole('tab', {name: 'Career memory'}).click(); await expect(memory.locator('article')).toContainText('Needs wording review');
  });
});

test('desktop and phone layouts have no horizontal overflow, keyboard tabs work and accessible names pass axe', async () => {
  await mkdir(screenshotDir, {recursive: true});
  for (const viewport of [{width: 1200, height: 900}, {width: 390, height: 844}]) await workspace(async page => {
    await call(page, 'saveStory', account); await page.reload();
    await expect(page.locator('#memory article')).toBeVisible();
    for (const [name, tab] of [['memory', 'Career memory'], ['capsules', 'Time capsules'], ['inbox', 'Unified inbox']]) {
      await page.getByRole('tab', {name: tab, exact: true}).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name} overflow at ${viewport.width}`);
      const violations = (await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations;
      assert.deepEqual(violations.map(value => ({id: value.id, description: value.description})), []);
      await page.screenshot({path: `${screenshotDir}/workspace-${name}-${viewport.width}.png`, fullPage: true});
    }
    await page.getByRole('tab', {name: 'Unified inbox'}).focus(); await page.keyboard.press('Home');
    await expect(page.getByRole('tab', {name: 'Career memory'})).toBeFocused();
    await page.keyboard.press('ArrowDown'); await expect(page.getByRole('tab', {name: 'Time capsules'})).toBeFocused();
  }, viewport);
});

test('real isolated MV3 broker permits workspace tabs and persists memory across extension pages', async () => {
  const profile = await mkdtemp(join(tmpdir(), 'jobpilot-workspace-mv3-'));
  const extensionPath = fileURLToPath(root);
  let context;
  try {
    context = await chromium.launchPersistentContext(profile, {channel: 'chromium', headless: true,
      args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]});
    const external = [];
    await context.route(/^https?:/, route => { external.push(route.request().url()); return route.abort(); });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    const extensionId = new URL(worker.url()).host;
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`chrome-extension://${extensionId}/workspace.html`);
    await expect(page.getByRole('heading', {name: 'Story Vault', exact: true})).toBeVisible();
    assert.deepEqual(await call(page, 'listStories'), []);
    await page.locator('#memory').getByLabel('Story title', {exact: true}).fill('Real broker synthetic account');
    await page.locator('#memory').getByLabel('What happened and your contribution').fill('Synthetic test data in an isolated profile.');
    await page.locator('#memory').getByLabel('I reviewed this wording').check();
    await page.locator('#memory').getByRole('button', {name: 'Save story', exact: true}).click();
    await expect(page.locator('#memory article')).toContainText('Wording reviewed');
    const second = await context.newPage(); await second.goto(`chrome-extension://${extensionId}/workspace.html`);
    await expect(second.locator('#memory article')).toContainText('Real broker synthetic account');
    const saved = await call(second, 'listStories'); assert.equal(saved.length, 1); assert.ok(saved[0].reviewedAt);
    await second.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    assert.equal((await call(second, 'listStories'))[0].title, saved[0].title);
    assert.deepEqual(errors, []); assert.deepEqual(external, [], 'real extension smoke must not access employer sites or external services');
  } finally { await context?.close(); await rm(profile, {recursive: true, force: true}); }
});
