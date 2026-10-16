import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, runInContext } from "node:vm";
import { test } from "node:test";

const source = (await readFile(new URL("../background.js", import.meta.url), "utf8"))
  .replace(/^import .*?;\s*/s, "");
const settle = () => new Promise(resolve => setImmediate(resolve));

function background({ access = async () => {}, open = async () => {} } = {}) {
  const listeners = {}, calls = [], errors = [];
  const event = name => ({ addListener: listener => { listeners[name] = listener; } });
  const context = createContext({
    console: { error: (...args) => errors.push(args) },
    initializeLocalData: async () => {},
    executeModelOperation: async () => {},
    chrome: {
      action: { onClicked: event("action") },
      storage: { local: {
        setAccessLevel: options => { calls.push(["access", { ...options }]); return access(); },
        set() { throw new Error("Toolbar invocation must not remember tab metadata"); },
      } },
      sidePanel: {
        setPanelBehavior: async options => { calls.push(["behavior", { ...options }]); },
        open: options => { calls.push(["open", { ...options }]); return open(); },
      },
      runtime: {
        id: "fixture", getURL: path => `chrome-extension://fixture/${path}`,
        onInstalled: event("installed"), onStartup: event("startup"), onMessage: event("message"),
      },
    },
  });
  runInContext(source, context);
  return { listeners, calls, errors };
}

test("setup explicitly disables the persisted automatic panel toggle on load, install and startup", async () => {
  const { listeners, calls } = background();
  await settle();
  for (const name of ["installed", "startup"]) {
    await listeners[name]();
  }
  assert.deepEqual(calls.filter(([kind]) => kind === "behavior"), [
    ["behavior", { openPanelOnActionClick: false }],
    ["behavior", { openPanelOnActionClick: false }],
    ["behavior", { openPanelOnActionClick: false }],
  ]);
});

test("the toolbar opens the clicked window synchronously even while startup work is pending", async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const { listeners, calls, errors } = background({ access: () => pending });
  await settle();
  assert.deepEqual(calls, [["access", { accessLevel: "TRUSTED_CONTEXTS" }]]);
  const tab = { windowId: 37 };
  for (const key of ["id", "url", "pendingUrl", "title"]) {
    Object.defineProperty(tab, key, { get() { throw new Error(`Unexpected tab metadata read: ${key}`); } });
  }
  assert.equal(listeners.action(tab), undefined);
  assert.deepEqual(calls.at(-1), ["open", { windowId: 37 }], "No queued task, query or await may precede opening");
  listeners.action({ windowId: 92 });
  assert.deepEqual(calls.at(-1), ["open", { windowId: 92 }], "Each click uses its own window");
  release();
  await settle();
  assert.deepEqual(errors, []);
});

test("a rejected or synchronously unavailable panel API is handled and the next toolbar click can retry", async () => {
  for (const failure of [() => Promise.reject(new Error("BROWSER_DETAIL")), () => { throw new Error("BROWSER_DETAIL"); }]) {
    let attempts = 0;
    const { listeners, errors } = background({ open: () => ++attempts === 1 ? failure() : Promise.resolve() });
    assert.doesNotThrow(() => listeners.action({ windowId: 4 }));
    await settle();
    assert.equal(errors.length, 1);
    assert.match(errors[0][0], /could not open the panel/);
    assert.equal(JSON.stringify(errors).includes("BROWSER_DETAIL"), false);
    listeners.action({ windowId: 4 });
    await settle();
    assert.equal(attempts, 2);
    assert.equal(errors.length, 1);
  }
});
