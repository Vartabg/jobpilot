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

function metadataDOM() {
  // A minimal DOM implements the selector/clone APIs used by the real collector,
  // including descendant removal and ancestor checks. No browser or user data.
  let document;
  function element(tag, attrs = {}, text = '', children = []) {
    const node = {tagName: tag.toUpperCase(), attrs: {...attrs}, text, children, parentElement: null, form:null,
      get ownerDocument() { return document; },
      get textContent() { return this.text + this.children.map(child => child.textContent).join(''); },
      get type() { return this.attrs.type || (this.tagName === 'INPUT' ? 'text' : ''); },
      get name() { return this.attrs.name || ''; },
      getAttribute(name) { return this.attrs[name] ?? null; }, hasAttribute(name) { return Object.hasOwn(this.attrs, name); },
      getClientRects() { return this.closest('[hidden]') ? [] : [{}]; },
      matches(selectors) {
        return selectors.split(',').some(selector => {
          let remaining = selector.trim();
          const tag = /^[\w-]+/.exec(remaining);
          if (tag) { if (this.tagName !== tag[0].toUpperCase()) return false; remaining = remaining.slice(tag[0].length); }
          while (remaining) {
            const attr = /^\[([\w-]+)(?:(\^?=)["']?([^\]"']+)["']?)?\]/.exec(remaining);
            if (!attr || !this.hasAttribute(attr[1])) return false;
            if (attr[2] === '=' && this.getAttribute(attr[1]) !== attr[3]) return false;
            if (attr[2] === '^=' && !this.getAttribute(attr[1]).startsWith(attr[3])) return false;
            remaining = remaining.slice(attr[0].length);
          }
          return true;
        });
      },
      closest(selectors) { for (let current = this; current; current = current.parentElement) if (current.matches(selectors)) return current; return null; },
      contains(target) { for (let current = target; current; current = current.parentElement) if (current === this) return true; return false; },
      querySelectorAll(selectors) { return this.children.flatMap(child => [...(child.matches(selectors) ? [child] : []), ...child.querySelectorAll(selectors)]); },
      querySelector(selectors) { return this.querySelectorAll(selectors)[0] || null; },
      cloneNode() { return element(this.tagName, this.attrs, this.text, this.children.map(child => child.cloneNode(true))); },
      remove() { this.parentElement.children = this.parentElement.children.filter(child => child !== this); },
    };
    for (const child of children) child.parentElement = node;
    return node;
  }
  return {element, async capture(children) {
    const body = element('body', {}, '', children);
    document = {contentType:'text/html', defaultView:{getComputedStyle:node => ({display:'block', visibility:'visible', opacity:'1',
      ...Object.fromEntries((node.attrs?.style || '').split(';').filter(Boolean).map(rule => rule.split(':').map(part => part.trim())))})},
      querySelectorAll: selector => body.querySelectorAll(selector),
      getElementById: id => body.querySelectorAll('[id]').find(node => node.getAttribute('id') === id) || null};
    const context = vm.createContext({document, location:{origin:'https://fixture.test', pathname:'/apply'}, Set});
    vm.runInContext(await readFile(new URL('../privacy-content.js', import.meta.url), 'utf8'), context);
    return JSON.parse(JSON.stringify(context.__jobpilotPrivacyCapture()));
  }};
}

test('Workday selection announcements never become help, findings, or exported scan metadata', async () => {
  const {element, capture} = metadataDOM();
  const privateChoices = ['ApplicantChoice-Z9 (+321)', 'PrivateSelection-K4', 'ApplicantSelection-Q7'];
  const announcement = element('span', {id:'announcement', 'data-automation-id':'promptAriaInstruction'},
    `1 item selected, ${privateChoices[0]}`, [element('span', {id:'announcement-child'}, privateChoices[0])]);
  const selected = element('div', {id:'selected', 'data-automation-id':'selectedItem'}, privateChoices[1]);
  const selectedList = element('ul', {'data-automation-id':'selectedItemList'}, '', [selected]);
  const publicHelp = 'Used only to route application follow-up.';
  const mixedHelp = element('div', {id:'help'}, publicHelp, [announcement, selectedList,
    element('span', {'aria-live':'polite'}, privateChoices[2]), element('span', {role:'alert'}, privateChoices[2])]);
  const label = element('label', {id:'code-label'}, 'Country Phone Code', [
    element('span', {'data-automation-id':'promptAriaInstruction'}, privateChoices[0])]);
  const control = element('input', {id:'phoneNumber--countryPhoneCode', required:'',
    'aria-labelledby':'code-label', 'aria-describedby':'announcement announcement-child selected help'});
  const address = element('input', {'aria-label':'Street address', 'aria-describedby':'help'});
  for (const input of [control, address]) for (const key of ['value', 'checked', 'files']) Object.defineProperty(input, key, {
    get() { throw new Error(`Applicant ${key} must not be read`); },
  });
  const payload = await capture([label, control, address, mixedHelp]);
  assert.deepEqual(payload.fields, [field('Country Phone Code', true, [], publicHelp), field('Street address', null, [], publicHelp)]);
  chromeMock({payloads:{top:{payload, document_type:'text/html', parent_visibility:'visible', visible_embeds:0}}});
  const report = await scanCurrentPrivacy();
  assert.ok(report.findings.length > 0, 'Actual address requests must still be reported');
  assert.ok(report.findings.every(finding => finding.employer_stated_purpose === publicHelp));
  const exported = JSON.stringify(report, null, 2);
  for (const privateChoice of privateChoices) {
    assert.equal(JSON.stringify(payload).includes(privateChoice), false, 'Raw field metadata excludes selected state');
    assert.equal(exported.includes(privateChoice), false, 'The exported report excludes selected state');
  }
  assert.equal(exported.includes('1 item selected'), false);
});

test('Workday button dropdowns expose only their local question, including when a private choice is selected', async () => {
  const {element, capture} = metadataDOM();
  const privateChoice = 'PRIVATE-DEGREE-CHOICE-X83';
  const dropdown = (id, question, withLabel = true, hidden = false) => {
    const button = element('button', {id, type:'button', 'aria-haspopup':'listbox', 'aria-label':`${question} ${privateChoice} Required`}, privateChoice);
    const getAttribute = button.getAttribute;
    button.getAttribute = function(name) { if (name === 'aria-label') throw new Error('Selected button label was read'); return getAttribute.call(this, name); };
    Object.defineProperty(button, 'textContent', {get() { throw new Error('Selected button text was read'); }});
    return element('div', {'data-automation-id':'formField-degree'}, '', [
      ...(withLabel ? [element('label', {for:id, ...(hidden ? {style:'display:none'} : {})}, question)] : []), button]);
  };
  const payload = await capture([dropdown('education-75--degree','Degree *'), dropdown('disclosure--gender','Gender identity *'),
    dropdown('unlabelled','PRIVATE-UNLINKED',false), dropdown('hidden-label','PRIVATE-HIDDEN',true,true)]);
  assert.deepEqual(payload.fields, [field('Degree *',true), field('Gender identity *',true), field('Unlabelled field')]);
  assert.ok(payload.notices.some(notice => /dropdown.*label/i.test(notice)));
  const report = analyzeFields(payload.fields);
  assert.deepEqual(report.findings.map(finding => finding.label), ['Gender identity *']);
  assert.equal(JSON.stringify({payload,report}).includes('PRIVATE'),false);
});

test('Workday role-less required radio questions are captured once with offered choices, without reading answers', async () => {
  const {element, capture} = metadataDOM();
  const question = 'Do you have a disability? *', help = 'The employer offers a separate decline choice.';
  const labels = ['Yes','No','Prefer not to answer'].map((choice,index) => {
    const input = element('input', {id:`choice-${index}`, type:'radio', name:'disability', style:'opacity:0'});
    for (const key of ['value','checked','selected','files']) Object.defineProperty(input,key,{get() { throw new Error(`Applicant ${key} was read`); }});
    const label = element('label', {}, choice, [input]); input.labels = [label]; return label;
  });
  const group = element('div', {'aria-required':'true', 'aria-labelledby':'radio-label4', 'aria-describedby':'radio-help'}, '', labels);
  const fieldNode = element('div', {'data-automation-id':'formField-disability'}, '', [element('label',{id:'radio-label4'},question), group,
    element('p',{id:'radio-help'},help)]);
  const payload = await capture([fieldNode]);
  assert.deepEqual(payload.fields,[field(question,true,['Yes','No','Prefer not to answer'],help)]);
  const report = analyzeFields(payload.fields);
  assert.equal(report.findings[0].category,'health_disability');
  assert.equal(report.findings[0].optionality,'response_required_disclosure_optional');
  assert.equal(JSON.stringify(report).includes('checked'),false);
});

test('Workday radio metadata never borrows a hidden, neighboring, mixed-name or different-form question', async t => {
  for (const invalid of ['external label','hidden label','mixed names','different forms']) await t.test(invalid,async () => {
    const {element,capture}=metadataDOM(), question='Do you have a disability? *';
    const inputs=['yes','no'].map(answer=>element('input',{type:'radio',name:'shared',id:answer}));
    const labels=inputs.map((input,index)=>{ const label=element('label',{},index?'No':'Yes',[input]); input.labels=[label]; return label; });
    if(invalid==='mixed names') inputs[1].attrs.name='neighbor';
    if(invalid==='different forms') inputs[1].form={};
    const title=element('label',{id:'question-label',...(invalid==='hidden label'?{style:'display:none'}:{})},question);
    const group=element('div',{'aria-required':'true','aria-labelledby':'question-label'},'',labels);
    const fieldNode=element('div',{'data-automation-id':'formField-question'},'',[...(invalid==='external label'?[]:[title]),group]);
    const payload=await capture([fieldNode,...(invalid==='external label'?[title]:[])]);
    assert.ok(payload.fields.every(item=>item.label!==question&&item.required!==true));
    assert.deepEqual(analyzeFields(payload.fields).findings,[]);
  });
});

test('telephone country-code labels do not imply location but residence and postal requests still do', () => {
  const labels = ['Country Phone Code', 'Phone Country Code *', 'Country Calling Code', 'CountryPhoneCode',
    'Country', 'Country of residence', 'Country code', 'Postal Code', 'Country phone code and country of residence'];
  const report = analyzeFields(labels.map(label => field(label, true)));
  assert.deepEqual(report.findings.map(finding => finding.label), labels.slice(4));
  assert.ok(report.findings.every(finding => finding.category === 'coarse_location'));
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
