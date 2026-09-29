import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, runInContext } from "node:vm";
import { test } from "node:test";

const source = (await readFile(new URL("../background.js", import.meta.url), "utf8"))
  .replace(/^import .*?;\s*/s, "");

function worker(execute) {
  let listener;
  const noopEvent = { addListener() {} };
  const context = createContext({
    console, executeModelOperation: execute, initializeLocalData: async () => ({ seeded: false }),
    chrome: {
      storage: { local: { setAccessLevel: async () => {} } },
      sidePanel: { setPanelBehavior: async () => {} },
      runtime: {
        id: "test-id", getURL: path => "chrome-extension://test-id/" + path,
        onInstalled: noopEvent, onStartup: noopEvent,
        onMessage: { addListener: fn => { listener = fn; } },
      },
    },
  });
  runInContext(source, context);
  const trusted = { id: "test-id", url: "chrome-extension://test-id/sidepanel.html" };
  const message = (operation, args, sender = trusted) => new Promise(resolve => {
    listener({ type: "jobpilot:model", operation, args }, sender, resolve);
  });
  return { message };
}

test("worker serializes updates from multiple panels so read-modify-write operations do not lose records", async () => {
  let records = [];
  const starts = [];
  const { message } = worker(async (_operation, [value]) => {
    starts.push(value);
    const snapshot = [...records];
    await new Promise(resolve => setTimeout(resolve, value === "first" ? 20 : 1));
    records = [...snapshot, value];
    return value;
  });
  const results = await Promise.all([message("saveApplication", ["first"]), message("saveApplication", ["second"])]);
  assert.deepEqual(starts, ["first", "second"]);
  assert.deepEqual(records, ["first", "second"]);
  assert.ok(results.every(result => result.ok));
});

test("worker refuses profile access from content scripts or external pages", async () => {
  let calls = 0;
  const { message } = worker(async () => { calls++; return {}; });
  for (const sender of [
    { id: "test-id", url: "https://boards.greenhouse.io/example", tab: { id: 1 } },
    { id: "other-id", url: "chrome-extension://other-id/sidepanel.html" },
    { id: "test-id", url: "https://example.org" },
  ]) {
    const result = await message("getProfile", [], sender);
    assert.equal(result.ok, false);
    assert.match(result.error, /only inside/i);
  }
  assert.equal(calls, 0);
});

test("a rejected model operation does not poison subsequent worker requests", async () => {
  const { message } = worker(async operation => {
    if (operation === "bad") throw new Error("Invalid record");
    return "saved";
  });
  const rejected = await message("bad", []);
  assert.equal(rejected.ok, false);
  const next = await message("saveProfile", []);
  assert.equal(next.ok, true);
  assert.equal(next.result, "saved");
});
