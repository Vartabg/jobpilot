import assert from 'node:assert/strict';
import {mkdtemp, readdir, readFile, writeFile, copyFile, mkdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {chromium} from '@playwright/test';

const source = fileURLToPath(new URL('../', import.meta.url));
const urls = ['https://fixture.wd5.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R123/apply/applyManually',
  'https://wd3.myworkdaysite.com/en-GB/recruiting/fixture/External/job/Austin/Engineer_R123/apply/applyManually'];
const info = `<main data-automation-id="applyFlowPage"><section data-automation-id="applyFlowMyInfoPage">
  <div data-automation-id="formField-legalNameSection_firstName"><label for="first">First Name</label><input id="first" required></div>
  <div data-automation-id="formField-email"><label for="email">Email Address</label><input id="email" type="email" value="existing@example.test"></div>
  <button id="next">Save and Continue</button></section></main>
  <script>document.querySelector('#next').onclick=()=>document.body.dataset.advanced='true'</script>`;
const experience = `<main data-automation-id="applyFlowPage"><section data-automation-id="applyFlowMyExpPage">
  <div data-automation-id="resumeUpload"><label>Resume/CV</label>
  <div data-automation-id="file-upload-drop-zone"><input type="file" data-automation-id="file-upload-input-ref" id="resume">
  <span data-automation-id="file-upload-item-name" id="uploaded"></span><span id="status"></span></div></div></section></main>
  <script>document.querySelector('#resume').onchange=e=>{document.querySelector('#uploaded').textContent=e.target.files[0]?.name||'';
  document.querySelector('#status').setAttribute('data-automation-id','file-upload-successful')}</script>`;

test('real MV3 scripting executes Workday capture, current-step fill and exact PDF attachment on both URL families', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'jobpilot-workday-mv3-'));
  const extension = join(folder, 'extension'); await mkdir(extension);
  const production = JSON.parse(await readFile(join(source, 'manifest.json'), 'utf8'));
  assert.deepEqual(production.permissions, ['storage', 'sidePanel', 'scripting', 'activeTab']);
  assert.equal(production.host_permissions.some(host => /workday/i.test(host)), false,
    'production uses the existing toolbar-triggered grant, not persistent Workday access');
  for (const entry of await readdir(source, {withFileTypes:true})) {
    if (entry.isFile() && /\.(js|css|html)$/.test(entry.name)) await copyFile(join(source,entry.name),join(extension,entry.name));
  }
  // The disposable test manifest supplies the same origin access that a toolbar
  // click grants via activeTab. This tests real Chrome scripting, not the grant UI.
  await writeFile(join(extension,'manifest.json'),JSON.stringify({...production, host_permissions:
    [...production.host_permissions, 'https://fixture.wd5.myworkdayjobs.com/*', 'https://wd3.myworkdaysite.com/*']}));
  let context;
  try {
    context = await chromium.launchPersistentContext(join(folder,'profile'), {channel:'chromium', headless:true,
      args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
    let body = info;
    await context.route(/^https?:/, route => urls.some(url => new URL(url).origin === new URL(route.request().url()).origin)
      && route.request().isNavigationRequest() ? route.fulfill({contentType:'text/html',body}) : route.abort());
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    const controller = await context.newPage();
    await controller.goto(`chrome-extension://${new URL(worker.url()).host}/sidepanel.html`);
    const page = await context.newPage();
    const run = action => controller.evaluate(async ({action,url}) => {
      const actions = await import('./actions.js');
      if (action === 'capture') return actions.captureCurrentJob();
      return actions.runCurrentAction(action, {first_name:'Alex',email:'alex@example.test'},
        {metadata:{name:'Exact.pdf'},dataUrl:'data:application/pdf;base64,JVBERi0xLjQK'}, {expectedUrl:url});
    }, {action,url:page.url()});
    for (const url of urls) {
      body = info; await page.goto(url); await page.bringToFront();
      assert.equal((await run('capture')).provider,'Workday');
      assert.equal((await run('inspect')).status,'complete');
      const filled = await run('details');
      assert.equal(await page.locator('#first').inputValue(),'Alex',JSON.stringify(filled));
      assert.equal(await page.locator('#email').inputValue(),'existing@example.test');
      assert.equal(await page.locator('body').getAttribute('data-advanced'),null);
      body = experience; await page.reload();
      const attached = await run('resume');
      assert.equal(attached.frames[0].resume.status,'accepted',JSON.stringify(attached));
      assert.equal(await page.locator('#resume').evaluate(input=>input.files[0]?.name),'Exact.pdf');
      assert.equal((await run('resume')).frames[0].resume.status,'already-attached');
    }
  } finally { await context?.close(); await rm(folder,{recursive:true,force:true}); }
});
