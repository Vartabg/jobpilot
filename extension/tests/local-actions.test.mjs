import assert from 'node:assert/strict';
import {beforeEach, test} from 'node:test';
import {createContext, runInContext} from 'node:vm';
import {localJob, routeLocalRequest} from '../local-actions.js';

const url = 'https://boards.greenhouse.io/example/jobs/123';
let tab, calls, received;
const request = (op, more = {}) => ({op, tabId: 7, expectedUrl: url, deadline: Date.now() + 60_000, ...more});
beforeEach(() => {
  tab = {id: 7, url, status: 'complete'}; calls = []; received = [];
  globalThis.chrome = {
    tabs: {get: async () => tab, query: async () => [tab], create: async options => { calls.push({create: options}); return {id: 9}; }},
    scripting: {executeScript: async options => {
      calls.push(options);
      if (options.files) return [{frameId: 0, documentId: 'document-one'}];
      received.push(options.args[0]);
      return [{frameId: 0, documentId: 'document-one', result: {status: 'inspected', snapshot: 'snapshot-one'}}];
    }},
  };
});

test('local routes require an actual stable ATS job and reject account, board, Workday and hostile origins', () => {
  for (const good of [url, 'https://job-boards.eu.greenhouse.io/example?gh_jid=123', 'https://jobs.lever.co/example/req-123/apply', 'https://jobs.ashbyhq.com/example/req-123/application']) assert.ok(localJob(good), good);
  for (const bad of ['https://boards.greenhouse.io/example', 'https://boards.greenhouse.io/example/jobs/no-id', `${url}?gh_jid=456`, 'https://jobs.lever.co/example/login', 'https://jobs.ashbyhq.com/example/req-123/anything', 'https://example.wd1.myworkdayjobs.com/External/job/Location/Engineer_R123/apply', 'https://boards.greenhouse.io.evil.example/example/jobs/123', 'http://boards.greenhouse.io/example/jobs/123', 'https://user:pass@boards.greenhouse.io/example/jobs/123', 'https://boards.greenhouse.io:8443/example/jobs/123']) assert.equal(localJob(bad), null, bad);
  assert.equal(localJob(url).identity, localJob(url.replace('boards.', 'job-boards.')).identity);
});

test('inspection pins the top document and returns its Chrome identity', async () => {
  const result = await routeLocalRequest(request('inspect'));
  assert.deepEqual(calls[0].target, {tabId: 7, frameIds: [0]});
  assert.deepEqual(calls[0].files, ['content.js', 'local-content.js']);
  assert.deepEqual(calls[1].target, {tabId: 7, documentIds: ['document-one']});
  assert.deepEqual(received, [{op: 'inspect', expectedUrl: url}]);
  assert.equal(result.documentId, 'document-one'); assert.equal(result.tabId, 7); assert.equal(result.snapshot, 'snapshot-one');
});

test('every mutation requires the inspected document and snapshot without frame fallback', async () => {
  for (const op of ['details', 'resume', 'answer', 'prepare-submit', 'submit']) {
    await assert.rejects(routeLocalRequest(request(op)), /documentId/);
    await assert.rejects(routeLocalRequest(request(op, {documentId: 'document-one'})), /snapshot/);
  }
  assert.equal(calls.length, 0);
  await routeLocalRequest(request('details', {documentId: 'document-one', snapshot: 'snapshot-one', profile: {first_name: 'Alex', disclosure_preferences: {veteran_status: 'decline'}}, resume: {private: true}, nonce: 'unused'}));
  assert.deepEqual(calls[0].target, {tabId: 7, documentIds: ['document-one']});
  assert.deepEqual(received[0], {op: 'details', expectedUrl: url, snapshot: 'snapshot-one', profile: {first_name: 'Alex'}});
});

test('explicit question decisions and resume bytes are isolated to their own operations', async () => {
  const bound = {documentId: 'document-one', snapshot: 'snapshot-one'};
  await routeLocalRequest(request('answer', {...bound, questionId: 'question-one', value: 'Yes', explicitPersonalDecision: true, replace: true, profile: {private: true}}));
  assert.deepEqual(received[0], {op: 'answer', expectedUrl: url, snapshot: 'snapshot-one', questionId: 'question-one', value: 'Yes', explicitPersonalDecision: true, replace: true});
  const resume = {metadata: {name: 'Resume.pdf'}, dataUrl: 'data:application/pdf;base64,JVBERi0='};
  await routeLocalRequest(request('resume', {...bound, resume, profile: {private: true}}));
  assert.deepEqual(received[1], {op: 'resume', expectedUrl: url, snapshot: 'snapshot-one', resume});
  await assert.rejects(routeLocalRequest(request('resume', {...bound, resume: {dataUrl: 'data:application/pdf;base64,bm90IGEgcGRm'}})), /explicit PDF/);
  await assert.rejects(routeLocalRequest(request('resume', {...bound, resume: {dataUrl: `data:application/pdf;base64,${'AAAA'.repeat(180_000)}`}})), /512 KiB/);
});

test('wrong tabs, same-job URL changes, pending navigation and deadlines stop before injection', async () => {
  for (const changed of [{id: 8, url}, {id: 7, url: `${url}?step=next`}, {id: 7, url: url.replace('123', '456')}, {id: 7, url, pendingUrl: url.replace('123', '456')}, {id: 7, url, status: 'loading'}]) {
    tab = changed;
    await assert.rejects(routeLocalRequest(request('inspect')), /changed|navigating/);
  }
  await assert.rejects(routeLocalRequest(request('inspect', {deadline: Date.now() - 1})), /deadline/);
  await assert.rejects(routeLocalRequest(request('inspect', {tabId: undefined})), /tabId/);
  assert.equal(calls.length, 0);
});

test('a replacement document or navigation during injection never dispatches a mutation', async () => {
  chrome.scripting.executeScript = async options => { calls.push(options); return [{frameId: 0, documentId: 'replacement-document'}]; };
  await assert.rejects(routeLocalRequest(request('details', {documentId: 'document-one', snapshot: 'snapshot-one', profile: {first_name: 'Alex'}})), /top application document/);
  assert.equal(calls.length, 1);
  chrome.scripting.executeScript = async options => { calls.push(options); tab.url = `${url}?step=changed`; return [{frameId: 0, documentId: 'document-one'}]; };
  await assert.rejects(routeLocalRequest(request('inspect')), /URL changed/);
  assert.equal(calls.length, 2);
});

test('the injected runner checks top-document URL before calling the bounded API', async () => {
  await routeLocalRequest(request('inspect'));
  const options = calls[1]; let mutations = 0;
  const window = {};
  const context = createContext({window, top: window, location: {href: url.replace('123', '456')}, document: {contentType: 'text/html'}, payload: options.args[0], __jobpilotLocalRun: () => { mutations++; return {}; }});
  await assert.rejects(runInContext(`(${options.func.toString()})(payload)`, context), /document changed/);
  assert.equal(mutations, 0);
});

test('unknown browser results are never replayed or represented as success', async () => {
  chrome.scripting.executeScript = async options => { calls.push(options); if (options.files) return [{frameId: 0, documentId: 'document-one'}]; throw new Error('PRIVATE_BROWSER_ERROR'); };
  await assert.rejects(routeLocalRequest(request('details', {documentId: 'document-one', snapshot: 'snapshot-one', profile: {first_name: 'Alex'}})), error => /outcome is unknown/.test(error.message) && !error.message.includes('PRIVATE'));
  assert.equal(calls.length, 2);
});

test('static controller holds retain useful bounded diagnostics without exposing browser errors', async () => {
  await routeLocalRequest(request('inspect'));
  const options = calls[1], window = {};
  const context = createContext({window, top: window, location: {href: url}, document: {contentType: 'text/html'}, payload: options.args[0], __jobpilotLocalRun: () => { throw new Error('A visible CAPTCHA requires your input.'); }});
  const result = await runInContext(`(${options.func.toString()})(payload)`, context);
  assert.equal(result.localError, 'A visible CAPTCHA requires your input.');
  chrome.scripting.executeScript = async options => options.files ? [{frameId: 0, documentId: 'document-one'}] : [{frameId: 0, documentId: 'document-one', result: {localError: result.localError}}];
  await assert.rejects(routeLocalRequest(request('prepare-submit', {documentId: 'document-one', snapshot: 'snapshot-one'})), /CAPTCHA requires your input/);
});

test('tabs discloses only supported job identifiers; open rejects arbitrary URLs', async () => {
  chrome.tabs.query = async () => [tab, {id: 9, url: 'https://bank.example/private', title: 'Private'}, {id: 10, url: 'https://jobs.lever.co/example', title: 'Board'}, {id: 11, url: 'https://jobs.ashbyhq.com/example/req-123', title: 'Do not export title'}];
  const result = await routeLocalRequest(request('tabs'));
  assert.deepEqual(result.tabs.map(item => item.tabId), [7, 11]);
  assert.equal(JSON.stringify(result).includes('Private'), false);
  assert.ok(result.tabs.every(item => Object.keys(item).sort().join() === 'identity,provider,tabId,url'));
  await assert.rejects(routeLocalRequest(request('open', {url: 'https://example.org'})), /supported HTTPS/);
  const opened = await routeLocalRequest(request('open', {url}));
  assert.equal(opened.tabId, 9); assert.deepEqual(calls[0].create, {url, active: false});
});
