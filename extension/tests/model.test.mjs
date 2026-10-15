import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { emptyProfile, normalizeJobUrl, workdayJob, initializeLocalData, executeModelOperation as run } from "../model.js";

let data, writes;
beforeEach(() => {
  data = {}; writes = 0;
  globalThis.chrome = { runtime: { getURL: name => "https://extension.test/" + name, getManifest: () => ({version: '0.8.3'}) }, storage: { local: {
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

test('preferred name and address survive saving and backup without inferring missing values', async () => {
  const old=await run('saveProfile',[{first_name:'Alex',last_name:'Example'}]);
  for(const key of ['preferred_name','address_line1','address_line2']) assert.equal(old[key],'');
  const saved=await run('saveProfile',[{...old,preferred_name:' Lex ',address_line1:' 123 Example Street ',address_line2:' Unit 2 '}]);
  assert.equal(saved.preferred_name,'Lex');
  const backup=await run('exportData',[]);
  await run('importData',[backup,{replaceProfile:true}]);
  const restored=await run('getProfile',[]);
  assert.equal(restored.preferred_name,'Lex');
  assert.equal(restored.address_line1,'123 Example Street');
  assert.equal(restored.address_line2,'Unit 2');
});

test('Workday job identity is stable across locale, tracking and application steps, isolated by tenant/site/requisition', () => {
  const base = 'https://example.wd5.myworkdayjobs.com/External/job/Austin/Engineer_R123';
  const expected = normalizeJobUrl(base);
  for (const url of [base + '/apply', base + '/apply/applyManually?source=LinkedIn', base.replace('/External/', '/en-US/External/') + '?utm_source=feed']) {
    assert.deepEqual(normalizeJobUrl(url), expected);
  }
  assert.equal(expected.provider, 'workday');
  assert.equal(expected.identity, normalizeJobUrl(base.replace('/Austin/Engineer_R123', '/austin/engineer_r123') + '/apply/applyManually').identity);
  for (const url of [base.replace('example.', 'other.'), base.replace('/External/', '/Internal/'), base.replace('R123', 'R456')]) {
    assert.notEqual(normalizeJobUrl(url).identity, expected.identity);
  }
  const site = 'https://wd3.myworkdaysite.com/recruiting/example/External/job/Austin/Engineer_R123';
  assert.equal(normalizeJobUrl(site).identity, normalizeJobUrl(site.replace('/recruiting/', '/en-GB/recruiting/') + '/apply/autofillWithResume').identity);
  assert.notEqual(normalizeJobUrl(site).identity, normalizeJobUrl(site.replace('/example/', '/other/')).identity);
  for (const url of [base.replace('https:', 'http:'), base.replace('.com/', '.com.evil.test/'), base.replace('.com/', '.com:8443/'),
    'https://example.myworkday.com/External/job/1', 'https://example.wd5.myworkdayjobs.com/External/login',
    'https://wd3.myworkdaysite.com/recruiting/example/External', 'https://wd3.myworkdaysite.com/employee/example/External/job/1']) {
    assert.equal(workdayJob(url), null, url);
  }
});

test('pre-Workday tracker entries keep their ID, notes and submission status across application steps', async () => {
  const url = 'https://example.wd5.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R123';
  data.jobpilot_applications_v1 = [{id:'old-workday', company:'Example', title:'Engineer', url, provider:'website',
    identity:'website:' + url, status:'submitted', notes:'Existing notes', resume_id:'my-resume', resume_name:'Exact.pdf',
    createdAt:'2026-01-01T00:00:00.000Z', updatedAt:'2026-01-02T00:00:00.000Z', submittedAt:'2026-01-02T00:00:00.000Z'}];
  assert.equal((await run('findApplication', [url + '/apply/applyManually'])).id, 'old-workday');
  assert.equal(writes, 0, 'reading a legacy identity does not rewrite storage');
  const saved = await run('saveApplication', [{url: url + '/apply/applyManually'}, 'started']);
  assert.equal(saved.id, 'old-workday'); assert.equal(saved.status, 'submitted');
  assert.equal(saved.notes, 'Existing notes'); assert.equal(saved.resume_id, 'my-resume');
  assert.equal((await run('listApplications', [])).length, 1);
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
