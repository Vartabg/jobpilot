import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { captureCurrentJob, providerFor, runCurrentAction } from "../actions.js";

let tab, calls, reports;
beforeEach(() => {
  tab = { id: 7, url: "https://boards.greenhouse.io/example/jobs/123", title: "Engineer" };
  calls = [];
  reports = [{ frameId: 0, result: { action: "inspect", filled: [], required: [{ label: "Question" }] } },
    { frameId: 4, result: { action: "inspect", filled: [], required: [] } }];
  globalThis.chrome = {
    tabs: { query: async () => [tab] },
    scripting: { executeScript: async request => { calls.push(request); return request.files ? [{ frameId: 0 }] : reports; } },
  };
});

test("unsupported sites and non-HTTPS ATS pages never receive autofill injection", async () => {
  for (const url of ["https://example.org/jobs/1", "http://boards.greenhouse.io/example/jobs/123", "https://boards.greenhouse.io.evil.example/jobs/1", "chrome://extensions/"]) {
    tab.url = url;
    await assert.rejects(runCurrentAction("details", { first_name: "Alex" }));
    assert.equal(calls.length, 0);
  }
  assert.equal(providerFor("https://boards.greenhouse.io.evil.example/jobs/1"), "Manual");
});

test("details and inspect never send a supplied resume and frame reports are preserved", async () => {
  const privateResume = { metadata: { name: "Software.pdf" }, dataUrl: "data:application/pdf;base64,AA==" };
  for (const action of ["inspect", "details"]) {
    calls = [];
    const result = await runCurrentAction(action, { first_name: "Alex" }, privateResume);
    assert.deepEqual(calls[0].files, ["content.js"]);
    assert.equal(calls[1].args[0].resume, null);
    assert.equal(calls[1].args[0].action, action);
    assert.equal(result.frames.length, 2);
    assert.equal(result.frames[1].frameId, 4);
    assert.deepEqual(result.frames[0].required, [{ label: "Question" }]);
  }
});

test("resume action requires a local file and sends only the selected resume", async () => {
  await assert.rejects(runCurrentAction("resume", {}, { metadata: { name: "Missing.pdf", missingFile: true } }), /local file/i);
  assert.equal(calls.length, 0);
  const resume = { metadata: { id: "chosen", name: "Software.pdf" }, dataUrl: "data:application/pdf;base64,AA==" };
  await runCurrentAction("resume", {}, resume);
  assert.deepEqual(calls[1].args[0].resume, resume);
});

test("switching to a different requisition blocks a stale draft before any injection", async () => {
  await assert.rejects(runCurrentAction("details", { first_name: "Alex" }, null, {
    expectedUrl: "https://boards.greenhouse.io/example/jobs/456",
  }), /different job/i);
  assert.equal(calls.length, 0);
  await runCurrentAction("inspect", {}, null, {
    expectedUrl: tab.url + "?utm_source=tracking",
  });
  assert.equal(calls.length, 2, "a tracking parameter change is still the same requisition");
});

test("manual job capture remains available without invoking autofill", async () => {
  tab.url = "https://example.org/jobs/123";
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    return [{ frameId: 0, result: { title: "Engineer", company: "Example Co", description: "A job" } }];
  };
  const result = await captureCurrentJob();
  assert.equal(result.supported, false);
  assert.equal(result.provider, "Manual");
  assert.equal(result.company, "Example Co");
  assert.equal(calls.length, 1);
  assert.ok(calls[0].func);
  assert.equal(calls[0].files, undefined);
  assert.equal(calls[0].args, undefined, "job capture sends no profile or resume to the page");
});
