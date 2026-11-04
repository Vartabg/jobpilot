import assert from 'node:assert/strict';
import {test} from 'node:test';
import {installLocalBridge, ATTEMPTS_KEY, LOCAL_HOST} from '../local-bridge.js';

const url = 'https://boards.greenhouse.io/example/jobs/123';
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture(route = async request => ({operation: request.op}), saved = {}) {
  let listener, clock = Date.now(), connections = 0;
  const ports = [], events = () => { const listeners = []; return {addListener: callback => listeners.push(callback), emit: value => listeners.forEach(listener => listener(value))}; };
  globalThis.chrome = {
    storage: {local: {get: async key => ({[key]: saved[key]}), set: async patch => Object.assign(saved, patch)}},
    runtime: {id: 'fixture', getURL: name => `chrome-extension://fixture/${name}`, onMessage: {addListener: callback => { listener = callback; }}, connectNative: host => {
      assert.equal(host, LOCAL_HOST); connections++;
      const port = {onMessage: events(), onDisconnect: events(), posted: [], postMessage: message => port.posted.push(message), disconnect: () => port.onDisconnect.emit()};
      ports.push(port); return port;
    }},
  };
  const bridge = installLocalBridge({route, now: () => clock});
  const trusted = {id: 'fixture', url: 'chrome-extension://fixture/sidepanel.html'};
  const panel = (op, sender = trusted) => { let response; listener({type: 'jobpilot:local', op}, sender, value => { response = value; }); return response; };
  const send = (op, extra = {}) => { const request = {id: `request-${++fixture.next}`, op, deadline: clock + 60_000, ...extra}; ports.at(-1).onMessage.emit(request); return request; };
  const responses = async count => { for (let step = 0; step < 100 && ports.at(-1).posted.length < count; step++) await settle(); assert.equal(ports.at(-1).posted.length, count); return ports.at(-1).posted; };
  return {bridge, panel, send, responses, ports, saved, get connections() { return connections; }, advance: ms => { clock += ms; }, now: () => clock};
}
fixture.next = 0;

test('only exact bundled side-panel requests can create one native connection', () => {
  const f = fixture();
  assert.equal(f.connections, 0);
  for (const sender of [{id: 'fixture', url: 'https://boards.greenhouse.io/example/jobs/123'}, {id: 'other', url: 'chrome-extension://fixture/sidepanel.html'}, {id: 'fixture', url: 'chrome-extension://fixture/sidepanel.html#forged'}, {id: 'fixture', url: 'chrome-extension://fixture/workspace.html'}, {id: 'fixture', url: 'chrome-extension://fixture/sidepanel.html', tab: {id: 1}}]) assert.equal(f.panel('connect', sender).ok, false);
  assert.equal(f.connections, 0);
  assert.equal(f.panel('status').result.connected, false);
  assert.equal(f.panel('connect').result.connected, true);
  f.panel('connect'); assert.equal(f.connections, 1);
  assert.equal(f.panel('disconnect').result.connected, false);
  assert.equal(f.saved[ATTEMPTS_KEY], undefined);
});

test('native requests are serialized, correlated and duplicate IDs never replay', async () => {
  let release; const starts = [];
  const pending = new Promise(resolve => { release = resolve; });
  const f = fixture(async request => { starts.push(request.id); if (starts.length === 1) await pending; return {done: request.id}; });
  f.panel('connect'); const first = f.send('inspect'); const second = f.send('inspect');
  await settle(); assert.deepEqual(starts, [first.id]);
  release(); const results = await f.responses(2);
  assert.deepEqual(starts, [first.id, second.id]); assert.deepEqual(results.map(item => item.id), [first.id, second.id]);
  f.ports[0].onMessage.emit(first);
  assert.match((await f.responses(3))[2].error, /never replayed/); assert.equal(starts.length, 2);
});

test('expired queued work never dispatches and in-flight expiry remains unknown until settled', async () => {
  let release; const starts = [], pending = new Promise(resolve => { release = resolve; });
  const f = fixture(async request => { starts.push(request.id); await pending; return {attempted: true}; });
  f.panel('connect'); const first = f.send('details', {deadline: f.now() + 5}); f.send('answer', {deadline: f.now() + 5});
  await settle(); f.advance(10); await settle();
  assert.equal(f.ports[0].posted.length, 0, 'the uncertain action remains serialized until it settles');
  release(); const results = await f.responses(2);
  assert.deepEqual(starts, [first.id]); assert.match(results[0].error, /outcome is unknown/); assert.match(results[1].error, /queued request deadline/);
});

test('disconnect drops queued commands without reconnecting or replaying them', async () => {
  let release; let calls = 0; const pending = new Promise(resolve => { release = resolve; });
  const f = fixture(async () => { calls++; await pending; return {}; });
  f.panel('connect'); f.send('details'); f.send('answer'); await settle();
  f.panel('disconnect'); release(); await settle(); await settle();
  assert.equal(calls, 1); assert.equal(f.connections, 1); assert.equal(f.ports[0].posted.length, 0);
  assert.equal(f.bridge.status().connected, false);
});

test('an action throwing after its deadline is still reported as unknown', async () => {
  const f = fixture(async () => { f.advance(60_001); throw new Error('Expired during a page recheck'); });
  f.panel('connect'); f.send('details');
  assert.match((await f.responses(1))[0].error, /outcome is unknown/);
});

test('submission requires a known document-bound nonce and durable attempt before the only dispatch', async () => {
  let submits = 0;
  const saved = {};
  const f = fixture(async request => {
    if (request.op === 'prepare-submit') return {prepared: true, nonce: 'nonce-one', expiresAt: f.now() + 30_000, buttonLabel: 'Submit application'};
    if (request.op === 'submit') { submits++; assert.ok(saved[ATTEMPTS_KEY]['greenhouse:example:123']); throw new Error('Unknown browser outcome'); }
    return {};
  }, saved);
  const bound = {tabId: 7, expectedUrl: url, documentId: 'doc-one', snapshot: 'snapshot-one'};
  f.panel('connect');
  f.send('submit', {...bound, nonce: 'forged', confirm: true}); assert.match((await f.responses(1))[0].error, /preparation/); assert.equal(saved[ATTEMPTS_KEY], undefined);
  f.send('prepare-submit', bound); assert.equal((await f.responses(2))[1].ok, true);
  f.send('submit', {...bound, snapshot: 'wrong', nonce: 'nonce-one', confirm: true}); assert.match((await f.responses(3))[2].error, /another document/); assert.equal(submits, 0);
  f.send('submit', {...bound, nonce: 'nonce-one', confirm: true}); assert.equal((await f.responses(4))[3].ok, false); assert.equal(submits, 1);
  f.send('prepare-submit', bound); assert.match((await f.responses(5))[4].error, /recorded submission attempt/);
  f.panel('disconnect');
  const restarted = fixture(async () => { submits++; return {}; }, saved); restarted.panel('connect'); restarted.send('prepare-submit', bound);
  assert.match((await restarted.responses(1))[0].error, /recorded submission attempt/); assert.equal(submits, 1);
});

test('expired preparation and a failed attempt-storage write cannot dispatch submit', async () => {
  let submits = 0;
  const f = fixture(async request => request.op === 'prepare-submit' ? {prepared: true, nonce: 'nonce', expiresAt: f.now() + 10} : (submits++, {}));
  const bound = {tabId: 7, expectedUrl: url, documentId: 'doc-one', snapshot: 'snapshot-one'};
  f.panel('connect'); f.send('prepare-submit', bound); await f.responses(1); f.advance(11);
  f.send('submit', {...bound, nonce: 'nonce', confirm: true}); assert.match((await f.responses(2))[1].error, /expired/);
  f.send('prepare-submit', bound); await f.responses(3);
  chrome.storage.local.set = async () => { throw new Error('Storage failed'); };
  f.send('submit', {...bound, nonce: 'nonce', confirm: true}); assert.equal((await f.responses(4))[3].ok, false); assert.equal(submits, 0);
});

test('UTF-8 request and response limits fail explicitly without executing oversized input', async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return {large: 'é'.repeat(500_000)}; });
  f.panel('connect'); f.send('answer', {value: 'é'.repeat(500_000)});
  assert.match((await f.responses(1))[0].error, /900 KiB/); assert.equal(calls, 0);
  f.send('inspect'); assert.match((await f.responses(2))[1].error, /response exceeds/); assert.equal(calls, 1);
});
