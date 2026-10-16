import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {restorePrivacyReport, setupPrivacyPanel} from '../privacy-panel.js';

test('saved scans preserve separate frame provenance without restoring answers or token URLs', () => {
  const restored = restorePrivacyReport({source_url:'https://company.test/apply?token=private', checked_at:'2026-10-15T22:30:00Z', status:'partial',
    fields:[{label:'Legal Address',required:true,frame_id:0,frame_url:'https://company.test/apply?token=private',value:'PRIVATE ANSWER'},
      {label:'Legal Address',required:false,frame_id:4,frame_url:'https://ats.test/form?token=private'}],
    frames:[{frame_id:0,source_url:'https://company.test/apply',status:'inspected',field_count:1},
      {frame_id:4,source_url:'https://ats.test/form',status:'inspected',field_count:1}], notices:[]});
  assert.equal(restored.complete,false);
  assert.equal(restored.status,'partial');
  assert.deepEqual(restored.findings.map(f=>[f.frame_id,f.frame_url,f.optionality]),[
    [0,'https://company.test/apply','required'],[4,'https://ats.test/form','optional']]);
  assert.equal(restored.frames.length,2);
  assert.doesNotMatch(JSON.stringify(restored),/PRIVATE ANSWER|token=private/);
});

test('malformed saved data cannot become a successful scan',()=>{
  assert.equal(restorePrivacyReport({fields:Array(501).fill({label:'Gender'})}),null);
  assert.equal(restorePrivacyReport(null),null);
});

test('privacy initialization cannot leave Scan disabled when Capture is attempted during the saved-report read', async t => {
  // Use the real shared lifecycle, without starting a browser or importing the
  // side panel's auto-running UI. The minimal DOM models connected controls.
  const source = await readFile(new URL('../sidepanel.js', import.meta.url), 'utf8');
  const lifecycle = source.slice(source.indexOf('function setBusy(busy) {'), source.indexOf('\nfunction updateActionButtons()'));
  assert.match(lifecycle, /async function perform\(task\)/);
  for (const failRead of [false, true]) await t.test(failRead ? 'storage error remains retryable' : 'empty saved state remains ready', async () => {
    const previousDocument = globalThis.document, previousChrome = globalThis.chrome;
    const controls = new Map();
    const element = id => {
      if (!controls.has(id)) controls.set(id, {id, isConnected:true, disabled:['scan-privacy', 'privacy-export', 'privacy-clear'].includes(id),
        hidden:false, inert:false, addEventListener() {}, setAttribute() {}});
      return controls.get(id);
    };
    const mainControls = ['capture-job', 'inspect-form', 'fill-details', 'attach-resume', 'scan-privacy', 'privacy-export', 'privacy-clear', 'privacy-filter'].map(element);
    globalThis.document = {getElementById:element, body:element('body')};
    let finishRead, finishCapture;
    globalThis.chrome = {storage:{local:{get:() => new Promise((resolve, reject) => {
      finishRead = () => failRead ? reject(new Error('Synthetic storage error')) : resolve({});
    })}}};
    const perform = new Function('element', 'controls', `
      const state = {busy:false}; let busyControls = new Map();
      const $ = selector => element(selector === 'main' ? 'main' : selector.slice(1));
      const $$ = selector => selector.startsWith('main ') ? controls : [];
      const updateActionButtons = () => {}, showNotice = () => {}, friendlyError = error => error.message;
      ${lifecycle}
      return perform;
    `)(element, mainControls);
    try {
      const setup = setupPrivacyPanel({perform, showNotice() {}});
      const capture = perform(() => new Promise(resolve => { finishCapture = resolve; }));
      finishRead(); await setup;
      finishCapture?.(); await capture;
      assert.equal(element('scan-privacy').disabled, false, 'Scan must remain available after initialization and Capture unlock');
      assert.equal(element('main').inert, false);
      for (const id of ['capture-job', 'inspect-form', 'fill-details', 'attach-resume']) assert.equal(element(id).disabled, false, `${id} must unlock`);
      assert.equal(element('privacy-export').disabled, true, 'an empty report is not exportable');
      let captured = false; await perform(async () => { captured = true; });
      assert.equal(captured, true, 'Capture remains usable after privacy initialization');
      assert.equal(element('scan-privacy').disabled, false, 'later shared actions must not disable Scan permanently');
    } finally { globalThis.document = previousDocument; globalThis.chrome = previousChrome; }
  });
});
