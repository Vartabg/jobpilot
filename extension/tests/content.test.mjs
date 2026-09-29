import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { before, after, test } from "node:test";
import { chromium } from "@playwright/test";
import { captureCurrentJob } from "../actions.js";

const script = await readFile(new URL("../content.js", import.meta.url), "utf8");
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const profile = {
  first_name: "Alex", last_name: "Example", email: "alex@example.org",
  phone: "555-010-1200", city: "Austin", state: "TX", country: "United States",
  work_authorized: true, requires_sponsorship: false,
  linkedin_url: "https://www.linkedin.com/in/example", github_url: "https://github.com/example",
  portfolio_url: "https://example.org",
};
const resume = {
  metadata: { id: "test-resume", name: "Alex-Resume.pdf", type: "application/pdf", size: 26 },
  dataUrl: "data:application/pdf;base64," + Buffer.from("%PDF-1.4\nfixture resume\n%%EOF").toString("base64"),
};

async function fixture(html, run) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route("**/*", route => {
    if (route.request().isNavigationRequest()) return route.fulfill({ contentType: "text/html", body: html });
    return route.abort();
  });
  await page.goto("https://boards.greenhouse.io/example/jobs/123");
  await page.addScriptTag({ content: script });
  try { return await run(page); } finally { await context.close(); }
}
const execute = (page, action, extra = {}) => page.evaluate(
  options => globalThis.__jobpilotRun(options), { action, profile, ...extra },
);

test("details fills verified contact fields and preserves manual and unsafe answers", async () => {
  await fixture(`<form>
    <label>First name <input name="first_name" required></label>
    <label>Email <input type="email" name="email" value="manual@example.org" required></label>
    <label>Desired salary <input name="desired_salary" required></label>
    <label>Gender <select name="gender" required><option value="">Choose</option><option>Female</option></select></label>
    <label>Agree to terms <input name="consent" type="checkbox" required></label>
    <label>Authorized to work in the United States <select name="work_auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
  </form>`, async page => {
    const result = await execute(page, "details");
    assert.equal(await page.locator('[name="first_name"]').inputValue(), "Alex");
    assert.equal(await page.locator('[name="email"]').inputValue(), "manual@example.org");
    assert.equal(await page.locator('[name="desired_salary"]').inputValue(), "");
    assert.equal(await page.locator('[name="gender"]').inputValue(), "");
    assert.equal(await page.locator('[name="consent"]').isChecked(), false);
    assert.equal(await page.locator('[name="work_auth"]').inputValue(), "Yes");
    assert.equal(result.filled.length, 2);
    assert.equal(result.resume.status, "not-requested");
    assert.ok(result.required.some(item => /salary/i.test(item.label)));
    assert.ok(result.required.some(item => /gender/i.test(item.label)));
    assert.ok(result.required.some(item => /terms/i.test(item.label)));
  });
});

test("inspect reports missing radio groups, checkboxes, native and React controls without claiming fills", async () => {
  await fixture(`<form>
    <label>Salary expectations <input name="salary" required></label>
    <label>Gender <select name="gender" required><option value="">Choose</option><option>Female</option></select></label>
    <fieldset><legend>Citizenship *</legend><label><input type="radio" name="citizenship" value="yes" required>Yes</label><label><input type="radio" name="citizenship" value="no" required>No</label></fieldset>
    <label>Privacy consent <input type="checkbox" name="privacy" required></label>
    <div class="field"><label for="veteran">Veteran status *</label><div class="react-select__control"><input id="veteran" role="combobox" aria-required="true" aria-expanded="false"></div></div>
  </form>`, async page => {
    const result = await execute(page, "inspect");
    assert.equal(result.filled.length, 0);
    assert.ok(result.required.some(item => /salary/i.test(item.label)));
    assert.ok(result.required.some(item => /gender/i.test(item.label)));
    assert.equal(result.required.filter(item => item.type === "radio").length, 1);
    assert.ok(result.required.some(item => /privacy/i.test(item.label)));
    assert.ok(result.required.some(item => /veteran/i.test(item.label)));
    assert.equal(await page.locator('[name="privacy"]').isChecked(), false);
  });
});

test("unknown authorization remains unanswered instead of defaulting to yes", async () => {
  await fixture(`<label>Authorized to work in the United States <select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
    const result = await execute(page, "details", { profile: { ...profile, work_authorized: null } });
    assert.equal(await page.locator("select").inputValue(), "");
    assert.equal(result.filled.length, 0);
    assert.equal(result.required.length, 1);
  });
});

test("US work authorization does not answer a different country's question", async () => {
  await fixture(`<label>Authorized to work in Canada <select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
    const result = await execute(page, "details");
    assert.equal(await page.locator("select").inputValue(), "");
    assert.equal(result.filled.length, 0);
    assert.equal(result.required.length, 1);
  });
});

test("country-mismatched and country-unknown authorization stays manual", async () => {
  for (const [country, question] of [["Canada", "Authorized to work in the United States"], ["", "Authorized to work"]]) {
    await fixture(`<label>${question}<select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
      const result = await execute(page, "details", { profile: { ...profile, country } });
      assert.equal(await page.locator("select").inputValue(), "");
      assert.equal(result.filled.length, 0);
    });
  }
});

test("resume assignment is prepared, not employer acceptance, and leaves contact fields alone", async () => {
  await fixture(`<label>First name <input name="first_name"></label><div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"></div>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "prepared");
    assert.equal(await page.locator('[name="first_name"]').inputValue(), "");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files[0]?.name), "Alex-Resume.pdf");
  });
});

test("resume acceptance requires rendered attachment evidence", async () => {
  await fixture(`<div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"><div id="attachment"></div></div>
    <script>document.querySelector('#resume').addEventListener('change', event => {
      document.querySelector('#attachment').textContent = event.target.files[0].name;
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Remove resume'; document.querySelector('#attachment').append(button);
    });</script>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "accepted");
  });
});

test("an upload error defeats attachment appearance", async () => {
  await fixture(`<div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"><div id="attachment"></div></div>
    <script>document.querySelector('#resume').addEventListener('change', event => {
      document.querySelector('#attachment').innerHTML = '<span>Alex-Resume.pdf</span><button type="button">Remove resume</button><div role="alert" class="error">Upload failed: invalid file</div>';
    });</script>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "failed");
  });
});

test("a lone unrelated upload is never used as the resume target", async () => {
  await fixture(`<label>Cover letter <input type="file" name="cover_letter"></label>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "no-target");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files.length), 0);
  });
});

test("a preexisting resume file is preserved", async () => {
  await fixture(`<label>Resume <input type="file" name="resume"></label>`, async page => {
    await page.locator('[type="file"]').setInputFiles({ name: "Manual-Resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-fixture") });
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "already-attached");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files[0].name), "Manual-Resume.pdf");
  });
});

test("numbered history sections use verified row numbers, preserve manual titles, and skip ambiguous fields", async () => {
  const saved = { ...profile, work_history: [
    { company: "Current Co", title: "Current title", start_date: "2025-01", end_date: "2026-09", current: true },
    { company: "Previous Co", title: "Previous title", start_date: "2022-03", end_date: "2024-12", current: false },
  ], education: [{ school: "Example School", degree: "Certificate", field: "Computer Science", start_date: "2020", end_date: "2021" }] };
  await fixture(`<label>Company <input id="ambiguous-company"></label><label>Start date <input id="ambiguous-date"></label>
    <fieldset><legend>Work experience 2</legend><label>Company <input id="company-2"></label><label>Job title <input id="title-2" value="Manual title"></label><label>Start date <input type="month" id="start-2"></label><label>End date <input type="month" id="end-2"></label></fieldset>
    <fieldset><legend>Work experience 1</legend><label>Company <input id="company-1"></label><label>Job title <input id="title-1"></label><label>Start date <input type="month" id="start-1"></label><label>End date <input type="month" id="end-1"></label><label>Employer email <input id="employer-email"></label><label>City <input id="employer-city"></label></fieldset>
    <fieldset><legend>Education 1</legend><label>School <input id="school-1"></label><label>Degree <input id="degree-1"></label><label>Field of study <input id="field-1"></label><label>Email <input id="school-email"></label></fieldset>`, async page => {
    await execute(page, "details", { profile: saved });
    for (const [id, value] of [
      ["company-2", "Previous Co"], ["start-2", "2022-03"], ["end-2", "2024-12"], ["title-2", "Manual title"],
      ["company-1", "Current Co"], ["title-1", "Current title"], ["start-1", "2025-01"], ["end-1", ""],
      ["school-1", "Example School"], ["degree-1", "Certificate"], ["field-1", "Computer Science"],
      ["employer-email", ""], ["employer-city", ""], ["school-email", ""],
      ["ambiguous-company", ""], ["ambiguous-date", ""],
    ]) assert.equal(await page.locator("#" + id).inputValue(), value, id);
  });
});

test("job capture reads JSON-LD as data and does not execute description HTML", async () => {
  await fixture(`<h1>Fallback heading</h1>
    <script type="application/ld+json">not valid json</script>
    <script type="application/ld+json">{"@graph":[{"@type":["JobPosting"],"title":"Software Engineer","hiringOrganization":{"name":"Example Co"},"description":"<p>Build useful software.</p><img src=x onerror='globalThis.executed=true'>","identifier":{"value":"REQ-123"}}]}</script>`, async page => {
    const previous = globalThis.chrome;
    globalThis.chrome = {
      tabs: { query: async () => [{ id: 1, url: page.url(), title: "Job" }] },
      scripting: { executeScript: async request => [{ frameId: 0, result: await page.evaluate(
        source => (0, eval)("(" + source + ")")(), request.func.toString(),
      ) }] },
    };
    try {
      const result = await captureCurrentJob();
      assert.equal(result.title, "Software Engineer");
      assert.equal(result.company, "Example Co");
      assert.equal(result.request_id, "REQ-123");
      assert.ok(result.description.includes("Build useful software."));
      assert.equal(await page.evaluate(() => globalThis.executed), undefined);
    } finally { globalThis.chrome = previous; }
  });
});
