import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { createContext, runInContext } from 'node:vm';
import { captureCurrentJob, providerFor, runCurrentAction } from "../actions.js";

let tab, calls, reports;
const metadata = (frameId, hasForm = frameId === 0, extra = {}) => ({frameId,
  result: {pageUrl: tab.url, hasForm, isTop: frameId === 0, visibleEmbeds: [], ...extra}});
beforeEach(() => {
  tab = { id: 7, url: "https://boards.greenhouse.io/example/jobs/123", title: "Engineer" };
  calls = [];
  reports = [{ frameId: 0, result: { action: "inspect", filled: [], required: [{ label: "Question" }] } },
    { frameId: 4, result: { action: "inspect", filled: [], required: [] } }];
  globalThis.chrome = {
    tabs: { query: async () => [tab] },
    scripting: { executeScript: async request => {
      calls.push(request);
      if (request.args?.[0]?.probe) return reports.map(({frameId}) => metadata(frameId));
      return request.files ? reports.map(({frameId}) => ({frameId}))
        : reports.filter(item => request.target.frameIds.includes(item.frameId));
    } },
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
    assert.equal(calls[1].args[0].probe, 'jobpilot-frames');
    assert.equal(calls[1].args[0].profile, undefined, 'preflight carries no profile');
    assert.equal(calls[2].args[0].resume, null);
    assert.equal(calls[2].args[0].action, action);
    assert.equal(result.frames.length, action === 'inspect' ? 2 : 1);
    if (action === 'inspect') {
      assert.equal(result.frames[1].frameId, 4);
      assert.deepEqual(calls[2].args[0].profile, {}, 'inspection needs no profile data');
    }
    assert.deepEqual(result.frames[0].required, [{ label: "Question" }]);
  }
});

test("resume action requires a local file and sends only the selected resume", async () => {
  await assert.rejects(runCurrentAction("resume", {}, { metadata: { name: "Missing.pdf", missingFile: true } }), /local file/i);
  assert.equal(calls.length, 0);
  const resume = { metadata: { id: "chosen", name: "Software.pdf" }, dataUrl: "data:application/pdf;base64,AA==" };
  await runCurrentAction("resume", {}, resume);
  assert.deepEqual(calls[2].args[0].resume, resume);
});

test("switching to a different requisition blocks a stale draft before any injection", async () => {
  await assert.rejects(runCurrentAction("details", { first_name: "Alex" }, null, {
    expectedUrl: "https://boards.greenhouse.io/example/jobs/456",
  }), /different job/i);
  assert.equal(calls.length, 0);
  await runCurrentAction("inspect", {}, null, {
    expectedUrl: tab.url + "?utm_source=tracking",
  });
  assert.equal(calls.length, 4, "a tracking parameter change is still the same requisition");
});

test("a removed frame preserves completed inspection results and checkpoints them without rerunning", async () => {
  const checkpoints = [];
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    if (request.files) return [{frameId: 0, documentId: 'main-document'}, {frameId: 4, documentId: 'child-document'}];
    if (request.args?.[0]?.probe) return [{...metadata(0), documentId: 'main-document'}, {...metadata(4), documentId: 'child-document'}];
    if (request.target.documentIds[0] === 'child-document') throw new Error('No document with id child-document');
    return [{frameId: 0, result: {status: 'complete', filled: [{label: 'First name'}], skipped: [], failed: [], required: []}}];
  };
  const result = await runCurrentAction('inspect', {first_name: 'Alex'}, null, {onProgress: report => checkpoints.push(report)});
  assert.equal(result.status, 'partial');
  assert.deepEqual(result.frames[0].filled, [{label: 'First name'}]);
  assert.equal(result.frames[1].status, 'interrupted');
  assert.match(result.frames[1].error, /changed or disconnected/i);
  assert.equal(checkpoints.length, 2);
  assert.equal(checkpoints[0].frames.length, 1, 'the first known result is retained before the next document action');
  assert.equal(calls.filter(call => call.args?.[0]?.action === 'inspect').length, 2, 'each inspected document runs once');
  assert.deepEqual(calls[2].target, {tabId: 7, documentIds: ['main-document']});
  assert.deepEqual(calls[3].target, {tabId: 7, documentIds: ['child-document']});
});

test("a disconnected injection produces an interrupted report without claiming zero fills as success", async () => {
  chrome.scripting.executeScript = async request => { calls.push(request); throw new Error('The tab was closed'); };
  const result = await runCurrentAction('details', {first_name: 'Alex'});
  assert.equal(result.status, 'interrupted');
  assert.equal(result.frames.length, 1);
  assert.equal(result.frames[0].status, 'interrupted');
  assert.equal(calls.length, 1, 'an uncertain operation is never silently replayed');
});

test("content-level partial results and warnings survive orchestration", async () => {
  reports = [{frameId: 0, result: {status: 'partial', filled: [{label: 'Email'}], failed: [{label: 'City', reason: 'Form re-rendered'}],
    warnings: ['Location still needs review'], required: [{label: 'City'}]}}];
  const result = await runCurrentAction('details', {email: 'alex@example.org'});
  assert.equal(result.status, 'partial');
  assert.equal(result.frames[0].filled.length, 1);
  assert.deepEqual(result.frames[0].warnings, ['Location still needs review']);
});

test("missing frame results remain interrupted rather than an empty successful form", async () => {
  chrome.scripting.executeScript = async request => request.files ? [{frameId: 0}] : [];
  const result = await runCurrentAction('inspect', {});
  assert.equal(result.status, 'interrupted');
  assert.match(result.frames[0].error, /inspect the current page/i);
});

test("navigation between the tab query and injection cannot fill the replacement job", async () => {
  let mutations = 0;
  chrome.scripting.executeScript = async request => {
    if (request.files) return [{frameId: 0, documentId: 'replacement-document'}];
    if (request.args?.[0]?.probe) return [{...metadata(0), documentId: 'replacement-document'}];
    const window = {};
    const context = createContext({URL, window, top: window,
      location: {href: 'https://boards.greenhouse.io/example/jobs/456'}, payload: request.args[0],
      __jobpilotRun: () => { mutations++; return {status: 'complete'}; }});
    return [{frameId: 0, result: await runInContext(`(${request.func.toString()})(payload)`, context)}];
  };
  const result = await runCurrentAction('details', {first_name: 'Alex'});
  assert.equal(mutations, 0, 'the replacement document cannot receive any field mutations');
  assert.equal(result.status, 'interrupted');
  assert.match(result.frames[0].error, /different job/i);
});

test("third-party and different-job frames never receive profile data or resume bytes", async () => {
  const resume = {metadata: {name: 'Private.pdf'}, dataUrl: 'data:application/pdf;base64,AA=='};
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    if (request.files) return [{frameId: 0}, {frameId: 4}]; // Chromium skips inaccessible CAPTCHA frames.
    if (request.args?.[0]?.probe) return [metadata(0), metadata(4, true, {pageUrl: 'https://boards.greenhouse.io/another/jobs/456'})];
    return [{frameId: request.target.frameIds[0], result: {status: 'complete', filled: [], failed: [], required: []}}];
  };
  const result = await runCurrentAction('resume', {email: 'private@example.org'}, resume);
  assert.equal(result.status, 'complete');
  const actions = calls.filter(call => call.args?.[0]?.action);
  assert.equal(actions.length, 1);
  assert.deepEqual(actions[0].target.frameIds, [0]);
  assert.deepEqual(actions[0].args[0].profile, {}, 'resume upload needs no contact profile');
  assert.deepEqual(actions[0].args[0].resume, resume);
});

test("multiple visible matching forms trigger public inspection without mutating either", async () => {
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    if (request.files) return [{frameId: 0}, {frameId: 4}];
    if (request.args?.[0]?.probe) return [metadata(0, true, {visibleEmbeds: [tab.url]}), metadata(4, true)];
    return [{frameId: request.target.frameIds[0], result: {action: request.args[0].action, status: 'complete', filled: [], required: [{label: 'Email'}]}}];
  };
  for (const action of ['details', 'resume']) {
    calls = [];
    const result = await runCurrentAction(action, {email: 'private@example.org'}, {metadata: {name: 'Private.pdf'}, dataUrl: 'data:application/pdf;base64,AA=='});
    assert.equal(result.status, 'interrupted');
    assert.match(result.warnings[0], /several visible application forms/i);
    assert.equal(result.frames.length, 2);
    for (const call of calls.filter(call => call.args?.[0]?.action)) {
      assert.equal(call.args[0].action, 'inspect');
      assert.deepEqual(call.args[0].profile, {});
      assert.equal(call.args[0].resume, null);
    }
  }
});

test("one visible matching embedded form can fill when the top page has no form", async () => {
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    if (request.files) return [{frameId: 0}, {frameId: 4}];
    if (request.args?.[0]?.probe) return [metadata(0, false, {visibleEmbeds: [tab.url]}), metadata(4, true)];
    return [{frameId: request.target.frameIds[0], result: {status: 'complete', filled: [{label: 'Email'}], failed: [], required: []}}];
  };
  const result = await runCurrentAction('details', {email: 'private@example.org'});
  assert.equal(result.status, 'complete');
  const actions = calls.filter(call => call.args?.[0]?.action);
  assert.equal(actions.length, 1);
  assert.deepEqual(actions[0].target.frameIds, [4]);
  assert.equal(actions[0].args[0].profile.email, 'private@example.org');
});

test("a matching hidden embedded form is not selected for mutation", async () => {
  chrome.scripting.executeScript = async request => {
    calls.push(request);
    if (request.files) return [{frameId: 0}, {frameId: 4}];
    if (request.args?.[0]?.probe) return [metadata(0, false), metadata(4, true)];
    return [{frameId: request.target.frameIds[0], result: {status: 'complete', filled: [], required: []}}];
  };
  const result = await runCurrentAction('details', {email: 'private@example.org'});
  assert.equal(result.status, 'interrupted');
  for (const call of calls.filter(call => call.args?.[0]?.action)) {
    assert.equal(call.args[0].action, 'inspect');
    assert.deepEqual(call.args[0].profile, {});
  }
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
