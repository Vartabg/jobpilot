import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { emptyProfile, normalizeJobUrl, initializeLocalData, executeModelOperation as run } from "../model.js";

let data, writes;
beforeEach(() => {
  data = {}; writes = 0;
  globalThis.chrome = { runtime: { getURL: name => "https://extension.test/" + name }, storage: { local: {
    get: async key => Object.fromEntries([key].flat().map(name => [name, structuredClone(data[name])])),
    set: async values => { writes++; Object.assign(data, structuredClone(values)); },
  } } };
});

test("optional first-use data initializes once and never overwrites an existing profile", async () => {
  let requests = 0;
  globalThis.fetch = async () => { requests++; return { ok: true, json: async () => ({ profile: { first_name: "Seeded" }, resumes: [] }) }; };
  assert.equal((await initializeLocalData()).seeded, true);
  assert.equal((await run("getProfile", [])).first_name, "Seeded");
  await run("saveProfile", [{ first_name: "Edited" }]);
  assert.equal((await initializeLocalData()).seeded, false);
  assert.equal((await run("getProfile", [])).first_name, "Edited");
  assert.equal(requests, 1);
  delete data.jobpilot_initialized_v1;
  assert.equal((await initializeLocalData()).reason, "existing_data");
  assert.equal(requests, 1);
});

test("a missing seed leaves an empty editable profile and an unsafe seed path changes nothing", async () => {
  globalThis.fetch = async () => ({ ok: false });
  assert.equal((await initializeLocalData()).reason, "no_seed");
  assert.equal((await run("getProfile", [])).work_authorized, null);
  data = {}; writes = 0;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ profile: { first_name: "Incoming" }, resumes: [{ path: "../private.pdf", name: "Resume.pdf" }] }) });
  await assert.rejects(initializeLocalData(), /inside the extension/i);
  assert.equal(writes, 0);
  assert.deepEqual(data, {});
});

test("profile preserves unknown authorization and retains only verified structured fields", async () => {
  const profile = await run("saveProfile", [{ first_name: " Alex ", education: [{ school: "Example School", field_of_study: "Computer Science", start_date: "2020-01" }],
    gender: "female", disability: "yes", desired_salary: 120000, years_of_experience: 10 }]);
  assert.equal(profile.first_name, "Alex");
  assert.equal(profile.work_authorized, null);
  assert.equal(profile.requires_sponsorship, null);
  assert.equal(profile.education[0].field, "Computer Science");
  for (const key of ["gender", "disability", "desired_salary", "years_of_experience"]) assert.ok(!(key in profile));
  assert.deepEqual(await run("getProfile", []), profile);
  await assert.rejects(run("saveProfile", [{ ...emptyProfile(), work_authorized: "yes" }]), /authorization/i);
});

test("job identity dedupes tracking parameters and apply paths while preserving different requisitions", () => {
  const greenhouse = normalizeJobUrl("https://boards.greenhouse.io/example/jobs/123?gh_src=campaign");
  const greenhouseNewHost = normalizeJobUrl("https://job-boards.greenhouse.io/example/jobs/123?utm_source=foo#apply");
  assert.equal(greenhouse.identity, greenhouseNewHost.identity);
  assert.notEqual(greenhouse.identity, normalizeJobUrl("https://boards.greenhouse.io/example/jobs/456").identity);
  assert.equal(normalizeJobUrl("https://jobs.lever.co/example/req-123/apply?source=foo").identity,
    normalizeJobUrl("https://jobs.lever.co/example/req-123").identity);
  assert.equal(normalizeJobUrl("https://jobs.ashbyhq.com/example/req-123/application?utm_campaign=a").identity,
    normalizeJobUrl("https://jobs.ashbyhq.com/example/req-123").identity);
  assert.throws(() => normalizeJobUrl("javascript:alert(1)"));
  assert.throws(() => normalizeJobUrl("https://user:password@example.org/job"));
});

test("submission is explicit and saving another started draft cannot downgrade it", async () => {
  const job = { company: "Example Co", title: "Software Engineer", url: "https://boards.greenhouse.io/example/jobs/123", resume_id: "resume-1", resume_name: "Software.pdf" };
  const started = await run("saveApplication", [job, "started"]);
  assert.equal(started.submittedAt, "");
  const submitted = await run("saveApplication", [{ ...job, url: job.url + "?utm_source=foo" }, "submitted"]);
  assert.equal(started.id, submitted.id);
  assert.ok(submitted.submittedAt);
  const recaptured = await run("saveApplication", [{ ...job, notes: "Reviewed again" }, "started"]);
  assert.equal(recaptured.status, "submitted");
  assert.equal(recaptured.submittedAt, submitted.submittedAt);
  assert.equal((await run("listApplications", [])).length, 1);
  assert.equal(recaptured.resume_name, "Software.pdf");
});

test("import validates the entire backup before changing any stored data", async () => {
  await run("saveProfile", [{ first_name: "Existing" }]);
  const original = structuredClone(data);
  const count = writes;
  await assert.rejects(run("importData", [{ schemaVersion: 1, profile: { first_name: "Incoming" }, resumes: [], applications: [
    { id: "valid", company: "Example", title: "Engineer", url: "https://example.org/job", status: "submitted" },
    { id: "bad", company: "Bad", title: "Bad", url: "javascript:alert(1)", status: "submitted" },
  ] }]), /http|URL/i);
  assert.equal(writes, count);
  assert.deepEqual(data, original);
});

test("import preserves an existing profile unless replacement is explicit and merges newest duplicate", async () => {
  await run("saveProfile", [{ first_name: "Existing" }]);
  const existing = await run("saveApplication", [{ company: "Example", title: "Engineer", url: "https://boards.greenhouse.io/example/jobs/123" }, "started"]);
  const backup = { schemaVersion: 1, profile: { first_name: "Incoming" }, applications: [{ ...existing, id: "external-id", status: "interview", updatedAt: "2099-01-01T00:00:00Z" }], resumes: [] };
  const merged = await run("importData", [backup]);
  assert.equal(merged.profilePreserved, true);
  assert.equal((await run("getProfile", [])).first_name, "Existing");
  const records = await run("listApplications", []);
  assert.equal(records.length, 1);
  assert.equal(records[0].id, existing.id);
  assert.equal(records[0].status, "interview");
  await run("importData", [backup, { replaceProfile: true }]);
  assert.equal((await run("getProfile", [])).first_name, "Incoming");
});

test("metadata-only restored resumes cannot be confused with uploaded file bytes", async () => {
  await run("importData", [{ schemaVersion: 1, profile: emptyProfile(), applications: [], resumes: [
    { id: "restored", name: "Software.pdf", label: "Software", type: "application/pdf", size: 42 },
  ] }]);
  const resumes = await run("listResumes", []);
  assert.equal(resumes[0].missingFile, true);
  const backup = await run("exportData", []);
  assert.equal(backup.schemaVersion, 1);
  assert.ok(!JSON.stringify(backup).includes("data:application/pdf"));
});

test("unknown operations and statuses are rejected without storage writes", async () => {
  await assert.rejects(run("constructor", []), /Unknown/);
  await assert.rejects(run("saveApplication", [{ url: "https://example.org/job" }, "hired-guessed"]), /Unknown application status/);
  assert.equal(writes, 0);
});
