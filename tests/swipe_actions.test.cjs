const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../gigs/swipe.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup({blocked = false, mail = false, target} = {}) {
  const events = [], elements = {};
  const popup = {opener: {}, closed: false, location: {replace(url) { events.push(['navigate', url]); }},
    document: {title: '', head: {innerHTML: ''}, body: {textContent: ''}},
    close() { this.closed = true; events.push(['close']); }};
  let settle;
  const response = new Promise(resolve => { settle = resolve; });
  const context = vm.createContext({
    document: {getElementById(id) { return elements[id] ||= {style: {}, hidden: false}; }},
    window: {open(url) { events.push(['open', url]); return blocked ? null : popup; },
      location: {set href(url) { events.push(['mailto', url]); }},
      matchMedia() { return {matches: false}; }},
    fetch(url, options) { if (url === '/api/meta') return Promise.resolve({ok: false});
      events.push(['fetch', url, options]); return response; },
    setTimeout(fn, delay) { if (delay <= 220) fn(); return 1; }, clearTimeout() {},
  });
  vm.runInContext(script, context);
  context.__card = {id: 'one', is_mailto: mail, apply_target: target || (mail ? 'mailto:test@invalid.test' : 'https://ats.invalid.test/apply')};
  context.__events = events;
  vm.runInContext("cards = [__card]; render = () => {}; flingNow = () => {}; toast = (...args) => __events.push(['toast', ...args]);", context);
  return {events, popup, settle, run: code => vm.runInContext(code, context)};
}

test('reserves a window during the tap, then navigates only after a successful save', async () => {
  const h = setup();
  const pending = h.run("decide('apply')");
  assert.deepEqual(h.events.map(e => e[0]), ['open', 'fetch']);
  assert.equal(h.events[0][1], 'about:blank');
  assert.equal(h.popup.opener, null);
  assert.equal(h.events[1][2].headers['X-JobPilot-Request'], '1');
  h.settle({ok: true}); await pending;
  assert.equal(h.events.find(e => e[0] === 'navigate')[1], 'https://ats.invalid.test/apply');
  assert.equal(h.run('i'), 1);
});

test('a failed save closes the reserved window and keeps the card', async () => {
  const h = setup();
  const pending = h.run("decide('apply')");
  h.settle({ok: false}); await pending;
  assert.equal(h.popup.closed, true);
  assert.equal(h.events.some(e => e[0] === 'navigate'), false);
  assert.equal(h.run('i'), 0);
  assert.equal(h.run('busy'), false);
});

test('a blocked popup does not persist an application decision', async () => {
  const h = setup({blocked: true});
  h.settle({ok: true}); await h.run("decide('apply')");
  assert.equal(h.events.some(e => e[0] === 'fetch'), false);
  assert.equal(h.run('i'), 0);
  assert.equal(h.run('busy'), false);
});

test('email opens through an explicit link after saving, preserving a fresh user gesture', async () => {
  const h = setup({mail: true});
  const pending = h.run("decide('apply')");
  assert.equal(h.events.some(e => ['mailto', 'open'].includes(e[0])), false);
  h.settle({ok: true}); await pending;
  const toast = h.events.find(e => e[0] === 'toast');
  assert.equal(toast[3], 'mailto:test@invalid.test');
});

test('a tab closed during saving leaves a usable follow-up link', async () => {
  const h = setup();
  const pending = h.run("decide('apply')");
  h.popup.closed = true;
  h.settle({ok: true}); await pending;
  assert.equal(h.events.find(e => e[0] === 'toast')[3], 'https://ats.invalid.test/apply');
});

test('a failed undo preserves its retry action without inserting a duplicate card', async () => {
  const h = setup();
  h.run('last = cards[0]; i = 1');
  const pending = h.run('undo()');
  h.settle({ok: false}); await pending;
  assert.equal(h.run('cards.length'), 1);
  assert.equal(h.run('last.id'), 'one');
});

test('invalid apply targets are rejected before opening or saving', async () => {
  const h = setup({target: 'javascript:alert(1)'});
  h.settle({ok: true}); await h.run("decide('apply')");
  assert.equal(h.events.some(e => ['fetch', 'open'].includes(e[0])), false);
  assert.equal(h.run('i'), 0);
});
