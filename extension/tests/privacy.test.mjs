import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import vm from 'node:vm';
import {analyzeFields} from '../privacy-analyzer.js';
import {scanCurrentPrivacy} from '../privacy-action.js';

const field = (label, required = null, options = [], description = '') => ({label, required, options, description});

test('sensitive categories, exact labels, requiredness and decline choices remain distinct', () => {
  const report = analyzeFields([
    field('Legal Address *', true), field('Gender identity', true, ['Woman', 'Prefer not to answer']),
    field('Sexual orientation', null, ['Decline to disclose']), field('Postal Code', false),
    field('Country of citizenship'), field('Political party affiliation'), field('Trade union membership'),
    field('Describe your experience supporting applicants with disabilities.', true),
    field('Describe your experience supporting applicants with disabilities and your medical history.', true),
    field('Résumé', false, [], 'We welcome all gender identities.'),
  ]);
  assert.equal(report.complete, false);
  assert.deepEqual(report.findings.map(({label, category, optionality}) => ({label, category, optionality})), [
    {label: 'Legal Address *', category: 'precise_address', optionality: 'required'},
    {label: 'Gender identity', category: 'gender_identity', optionality: 'response_required_disclosure_optional'},
    {label: 'Sexual orientation', category: 'sexual_orientation', optionality: 'unknown'},
    {label: 'Postal Code', category: 'coarse_location', optionality: 'optional'},
    {label: 'Country of citizenship', category: 'nationality_citizenship', optionality: 'unknown'},
    {label: 'Political party affiliation', category: 'political_affiliation', optionality: 'unknown'},
    {label: 'Trade union membership', category: 'union_membership', optionality: 'unknown'},
    {label: 'Describe your experience supporting applicants with disabilities and your medical history.', category: 'health_disability', optionality: 'required'},
  ]);
});

test('analyzer allowlist never reads answers or preserves query credentials', () => {
  const raw = field('Street address', true);
  for (const key of ['value', 'answer', 'selected', 'checked', 'files', 'defaults']) Object.defineProperty(raw, key, {
    get() { throw new Error(`An applicant answer was read: ${key}`); }, enumerable: true,
  });
  const report = analyzeFields([raw], {source_url: 'https://example.test/apply?token=SECRET#SECRET'});
  assert.equal(report.source_url, 'https://example.test/apply');
  assert.deepEqual(Object.keys(report.fields[0]), ['label', 'required', 'options', 'description']);
  assert.equal(JSON.stringify(report).includes('SECRET'), false);
  assert.equal(analyzeFields([], {source_url: 'https://name:SECRET@example.test/apply'}).source_url, '');
});

test('metadata collector excludes applicant widgets, values, selections and DOM mutations', async () => {
  const label = text => ({matches: () => false, closest: () => null,
    cloneNode: () => ({textContent: text, querySelectorAll: () => []})});
  const options = ['Woman', 'Man', 'Prefer not to answer'].map(textContent => {
    const option = {textContent};
    Object.defineProperty(option, 'selected', {get() { throw new Error('selected answer read'); }});
    return option;
  });
  const controls = [];
  const document = {contentType: 'text/html', defaultView: {getComputedStyle: () => ({display: 'block', visibility: 'visible'})},
    querySelectorAll: () => controls, getElementById: () => null,
    createElement() { throw new Error('DOM creation is forbidden'); }};
  const control = (question, tagName, attrs = {}, choices = []) => {
    const value = {ownerDocument: document, tagName, type: tagName === 'SELECT' ? 'select-one' : 'text',
      labels: [label(question)], options: choices,
      getClientRects: () => [{}], closest: () => null,
      getAttribute: key => attrs[key] ?? null, hasAttribute: key => Object.hasOwn(attrs, key),
      contains: () => false};
    for (const key of ['value', 'checked', 'files']) Object.defineProperty(value, key, {get() { throw new Error(`${key} answer read`); }});
    return value;
  };
  controls.push(control('Legal Address *', 'INPUT', {required: ''}), control('Gender identity', 'SELECT', {'aria-required': 'true'}, options));
  const context = vm.createContext({document, location: {origin: 'https://fixture.test', pathname: '/application'}, Set});
  const source = await readFile(new URL('../privacy-content.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);
  const report = JSON.parse(JSON.stringify(context.__jobpilotPrivacyCapture()));
  assert.deepEqual(report.fields, [field('Legal Address *', true), field('Gender identity', true, ['Woman', 'Man', 'Prefer not to answer'])]);
  assert.equal(report.source_url, 'https://fixture.test/application');
  assert.equal(report.notices.some(notice => /visible fields/.test(notice)), true);
  assert.equal(/navigator|fetch\(|chrome\.|\.showModal\(|\.append\(|\.value\b|\.checked\b|\.files\b/.test(source), false);
});

function chromeMock({frames = [{frameId: 0, documentId: 'top', result: {source_url: 'https://fixture.test/apply', document_type: 'text/html'}}],
  payloads = {}, afterQuery = null, injectionError = false} = {}) {
  const calls = [], tab = {id: 42, url: 'https://fixture.test/apply?session=SECRET', status: 'complete'};
  let queries = 0;
  globalThis.chrome = {tabs: {query: async options => { calls.push({kind: 'query', options}); queries += 1; return [afterQuery?.(queries, tab) || {...tab}]; }},
    scripting: {executeScript: async options => {
      calls.push({kind: 'execute', options});
      assert.equal(options.args, undefined, 'No profile, résumé, answers, or payload is sent into a page');
      if (options.files) { if (injectionError) throw new Error('SECRET-BROWSER-ERROR'); return frames; }
      assert.equal(options.target.frameIds, undefined, 'Capture must bind to original document IDs');
      const documentId = options.target.documentIds[0], frame = frames.find(item => item.documentId === documentId);
      const value = payloads[documentId] ?? {payload: {source_url: frame.result.source_url, fields: [field('Legal Address', true)], notices: []}, document_type: 'text/html', parent_visibility: 'visible', visible_embeds: 0};
      if (value instanceof Error) throw value;
      return [{documentId, frameId: frame.frameId, result: value}];
    }}};
  return {calls, tab};
}

test('native scan reads each document by ID and preserves per-frame provenance without answers', async () => {
  const frames = [{frameId: 0, documentId: 'top', result: {source_url: 'https://fixture.test/apply', document_type: 'text/html'}},
    {frameId: 9, documentId: 'embedded', result: {source_url: 'https://forms.test/jobs/1', document_type: 'text/html'}}];
  const embedded = field('Gender identity', true, ['Prefer not to answer']);
  embedded.value = 'SECRET-APPLICANT';
  const {calls} = chromeMock({frames, payloads: {embedded: {payload: {source_url: 'https://forms.test/jobs/1', fields: [embedded], notices: []},
    document_type: 'text/html', parent_visibility: 'unknown', visible_embeds: 0}}});
  const report = await scanCurrentPrivacy();
  assert.equal(report.complete, false);
  assert.equal(report.source_url, 'https://fixture.test/apply');
  assert.equal(report.field_count, 2);
  assert.deepEqual(report.findings.map(finding => [finding.category, finding.frame_id, finding.frame_url]), [
    ['precise_address', 0, 'https://fixture.test/apply'], ['gender_identity', 9, 'https://forms.test/jobs/1']]);
  assert.deepEqual(calls.filter(call => call.kind === 'execute').map(call => call.options.target), [
    {tabId: 42, allFrames: true}, {tabId: 42, documentIds: ['top']}, {tabId: 42, documentIds: ['embedded']}]);
  assert.equal(JSON.stringify(report).includes('SECRET'), false);
  assert.equal(report.notices.some(notice => /embedding visibility/.test(notice)), true);
});

test('frame failures, missing document IDs and truncation remain explicitly incomplete', async () => {
  const frames = Array.from({length: 10}, (_, i) => ({frameId: i, documentId: `doc${i}`, result: {
    source_url: `https://fixture.test/frame${i}`, document_type: 'text/html'}}));
  delete frames[2].documentId;
  const {calls} = chromeMock({frames, payloads: {doc1: new Error('SECRET-FRAME-ERROR')}});
  const report = await scanCurrentPrivacy();
  assert.equal(report.status, 'partial');
  assert.equal(report.frames.length, 8);
  assert.equal(report.frames[1].status, 'unavailable');
  assert.equal(report.frames[2].status, 'unavailable');
  assert.equal(report.notices.some(notice => /eight accessible frames/.test(notice)), true);
  assert.equal(JSON.stringify(report).includes('SECRET'), false);
  assert.equal(calls.filter(call => call.kind === 'execute' && call.options.target.documentIds).length, 7);
});

test('navigation, access failures and non-webpages never produce an all-clear report', async () => {
  chromeMock({afterQuery: (count, tab) => count > 2 ? {...tab, url: 'https://fixture.test/next-step'} : null});
  await assert.rejects(scanCurrentPrivacy(), /changed during the privacy scan/);
  chromeMock({injectionError: true});
  await assert.rejects(scanCurrentPrivacy(), error => /toolbar icon/.test(error.message) && !/SECRET/.test(error.message));
  for (const url of ['chrome://extensions', 'file:///tmp/test.html', 'https://fixture.test/resume.pdf']) {
    const {tab, calls} = chromeMock(); tab.url = url;
    await assert.rejects(scanCurrentPrivacy());
    assert.equal(calls.some(call => call.kind === 'execute'), false);
  }
});

test('no visible metadata gives an unknown-coverage notice; it never means no personal requests', async () => {
  chromeMock({payloads: {top: {payload: {source_url: 'https://fixture.test/apply', fields: [], notices: []},
    document_type: 'text/html', parent_visibility: 'visible', visible_embeds: 1}}});
  const report = await scanCurrentPrivacy();
  assert.equal(report.field_count, 0);
  assert.equal(report.complete, false);
  assert.equal(report.notices.some(notice => /does not establish/.test(notice)), true);
  assert.equal(report.notices.some(notice => /embedded frames/.test(notice)), true);
});

test('aggregate capture remains bounded to 500 metadata fields across frames', async () => {
  const frames = [{frameId: 0, documentId: 'top', result: {source_url: 'https://fixture.test/apply', document_type: 'text/html'}},
    {frameId: 1, documentId: 'child', result: {source_url: 'https://forms.test/apply', document_type: 'text/html'}},
    {frameId: 2, documentId: 'extra', result: {source_url: 'https://forms.test/extra', document_type: 'text/html'}}];
  const payload = (source_url, count) => ({payload: {source_url, fields: Array.from({length: count}, (_, index) => field(`Legal Address ${index}`, true)), notices: []},
    document_type: 'text/html', parent_visibility: 'visible', visible_embeds: 0});
  const {calls} = chromeMock({frames, payloads: {top: payload('https://fixture.test/apply', 300), child: payload('https://forms.test/apply', 300)}});
  const report = await scanCurrentPrivacy();
  assert.equal(report.field_count, 500);
  assert.equal(report.findings.length, 500);
  assert.equal(report.frames[1].field_count, 200);
  assert.equal(report.frames[2].status, 'unavailable');
  assert.equal(report.status, 'partial');
  assert.equal(report.notices.some(notice => /500 fields across all frames/.test(notice)), true);
  assert.equal(calls.filter(call => call.kind === 'execute' && call.options.target.documentIds).length, 2);
});
