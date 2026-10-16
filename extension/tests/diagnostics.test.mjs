import assert from 'node:assert/strict';
import {beforeEach, test} from 'node:test';
import {sanitizeRun} from '../diagnostics.js';
import {executeModelOperation as run} from '../model.js';

let data;
beforeEach(() => {
  data = {};
  globalThis.chrome = {runtime: {getManifest: () => ({version: '0.8.3'})}, storage: {local: {
    get: async key => ({[key]: structuredClone(data[key])}),
    set: async values => Object.assign(data, structuredClone(values)),
  }}};
});
const input = (id = 'run-1') => ({id, action: 'details', job: {company: 'Example', title: 'Engineer',
  url: 'https://jobs.ashbyhq.com/example/requisition/application?token=private#secret'},
  report: {status: 'complete', frames: [{frameId: 0, filled: [{label: 'Email'}],
    required: [{label: 'Salary', type: 'text'}], skipped: [{label: 'Salary', reason: 'Answer this question yourself'}]}]}});

test('diagnostic allowlist excludes answers, profile, upload details, HTML, credentials and raw exceptions', () => {
  const record = input();
  record.profile = {email: 'private@example.org', address_line1: '42 Private Lane'};
  record.job.notes = 'Private note';
  Object.assign(record.report, {html: '<input value="private">', error: 'secret exception at /Users/alex/private.pdf'});
  Object.assign(record.report.frames[0], {pageUrl: 'https://private.test/?password=secret',
    failed: [{label: 'Contact private@example.org +1 (312) 555-1234', value: 'Secret answer', reason: 'Exception with personal data'}],
    resume: {status: 'failed', filename: 'private-resume.pdf', dataUrl: 'data:application/pdf;base64,SECRET', message: 'Another secret'}});
  const saved = sanitizeRun(record, null, '0.8.3');
  const json = JSON.stringify(saved);
  for (const secret of ['private@example.org', 'Private Lane', 'Private note', '<input', '/Users/', 'Secret answer', 'private-resume', 'base64', '?token', '#secret', '555-1234', 'Exception with']) assert.ok(!json.includes(secret), secret);
  assert.equal(saved.url, 'https://jobs.ashbyhq.com/example/requisition');
  assert.equal(saved.error, 'unknown');
  assert.equal(saved.frames[0].failed[0].reason, 'unknown');
  assert.match(saved.frames[0].failed[0].label, /email omitted/);
  assert.match(saved.frames[0].failed[0].label, /number omitted/);
});

test('manual choices and missing facts are distinct from unmapped controls and failed uploads', () => {
  const record = input();
  assert.equal(sanitizeRun(record).health, 'manual');
  record.report.frames[0].skipped[0].reason = 'No saved profile value';
  assert.equal(sanitizeRun(record).health, 'manual');
  record.report.frames[0].skipped[0].reason = 'No verified mapping for this question';
  assert.equal(sanitizeRun(record).health, 'problem');
  for (const status of ['failed', 'prepared', 'no-target']) {
    record.report.frames[0].skipped = [];
    record.report.frames[0].resume = {status};
    assert.equal(sanitizeRun(record).health, 'problem');
  }
  record.report = {status: 'interrupted', frames: []};
  assert.equal(sanitizeRun(record).health, 'problem');
});

test('URL diagnostics retain public requisitions but omit unrelated paths and all queries', () => {
  for (const [url, expected] of [
    ['https://boards.greenhouse.io/example/embed/job_app?gh_jid=123&email=secret', 'https://boards.greenhouse.io/example/jobs/123'],
    ['https://jobs.lever.co/example/req-123/apply?token=secret', 'https://jobs.lever.co/example/req-123'],
    ['https://example.org/private/path?token=secret', 'https://example.org/'],
    ['https://user:password@jobs.ashbyhq.com/example/req/application#secret', 'https://jobs.ashbyhq.com/example/req'],
    ['file:///Users/private.pdf', ''],
  ]) assert.equal(sanitizeRun({report: {pageUrl: url}}).url, expected);
});

test('Workday run links retain the public requisition without application, account or token paths', () => {
  for (const [source, expected] of [
    ['https://workiva.wd503.myworkdayjobs.com/en-US/careers/job/USA---Remote/Software-Support-Engineer_R12637/apply/autofillWithResume?token=PRIVATE#PRIVATE',
      'https://workiva.wd503.myworkdayjobs.com/careers/job/USA---Remote/Software-Support-Engineer_R12637'],
    ['https://wd3.myworkdaysite.com/en-GB/recruiting/example/External/job/Austin/Engineer_R123/apply/PRIVATE?token=PRIVATE',
      'https://wd3.myworkdaysite.com/recruiting/example/External/job/Austin/Engineer_R123'],
    ['https://example.wd5.myworkdayjobs.com/External/job/Engineer_R123',
      'https://example.wd5.myworkdayjobs.com/External/job/Engineer_R123'],
  ]) {
    const result = sanitizeRun({report:{pageUrl:source}});
    assert.equal(result.url,expected); assert.equal(result.provider,'Workday');
    assert.doesNotMatch(JSON.stringify(result),/PRIVATE|autofillWithResume|\/apply\//);
  }
  for (const source of [
    'https://workiva.wd503.myworkdayjobs.com/careers/passwordreset/PRIVATE',
    'https://workiva.wd503.myworkdayjobs.com/careers/job/Austin/Engineer_R123/PRIVATE',
    'https://wd3.myworkdaysite.com/recruiting/example/External/login/PRIVATE',
    'https://employee.workday.com/External/job/Engineer_R123',
    'https://example.wd5.myworkdayjobs.com.evil.test/External/job/Engineer_R123',
    'https://user:password@example.wd5.myworkdayjobs.com/External/job/Engineer_R123',
    'https://example.wd5.myworkdayjobs.com:8443/External/job/Engineer_R123',
  ]) {
    const result = sanitizeRun({report:{pageUrl:source}});
    assert.equal(result.provider,'Manual');
    assert.equal(new URL(result.url).pathname,'/');
    assert.doesNotMatch(result.url,/PRIVATE|password|Engineer_R123/);
  }
});

test('a known Workday manual-step guard remains explanatory in saved history', () => {
  const result = sanitizeRun({report:{status:'interrupted',warnings:[
    'Only the current visible Workday step is supported. Review it and move to the next step yourself.',
  ]}});
  assert.deepEqual(result.warnings,['workday_step']);
  assert.equal(result.health,'problem');
});

test('a split phone field is saved as a manual format check rather than an unexpected failure', () => {
  const result = sanitizeRun({report:{status:'complete',frames:[{skipped:[{label:'Phone Number',
    reason:'This phone field has a separate country code. Review its format and enter it yourself.'}]}]}});
  assert.equal(result.frames[0].skipped[0].reason,'phone_format');
  assert.equal(result.health,'manual');
});

test('an unselected Workday multi-select remains an explanatory manual choice in saved history', () => {
  const result = sanitizeRun({report:{status:'complete',frames:[{skipped:[{label:'Field of Study',
    reason:'This Workday multi-select needs a manual choice. Review it on the application page.'}]}]}});
  assert.equal(result.frames[0].skipped[0].reason,'workday_multiselect');
  assert.equal(result.health,'manual');
});

test('checkpoints replace their run, retain review state, and new attempts keep the last 50', async () => {
  const first = input(); first.report.status = 'running';
  await run('saveRun', [first]);
  first.report.status = 'partial'; first.report.frames[0].failed = [{label: 'Location', reason: 'This control could not be filled. Other supported fields were still checked.'}];
  await run('saveRun', [first]);
  await run('updateRun', ['run-1', {reported: true, resolved: true, profile: {email: 'secret'}}]);
  const saved = await run('saveRun', [first]);
  assert.equal(saved.version, '0.8.3');
  assert.equal(saved.reported, true); assert.equal(saved.resolved, true);
  assert.equal((await run('listRuns', [])).length, 1);
  for (let i = 2; i <= 51; i++) await run('saveRun', [input(`run-${i}`)]);
  const records = await run('listRuns', []);
  assert.equal(records.length, 50);
  assert.equal(records[0].id, 'run-51');
  assert.ok(!records.some(item => item.id === 'run-1'));
  await assert.rejects(run('updateRun', ['run-1', {resolved: true}]), /no longer/);
});

test('large forms keep failure evidence and bound diagnostic size', () => {
  const record = input();
  record.report.frames[0].filled = Array.from({length: 1000}, () => ({label: 'x'.repeat(1000)}));
  record.report.frames[0].failed = [{label: 'Location', reason: 'This field changed or is ambiguous. Review it manually.'}];
  const saved = sanitizeRun(record);
  assert.equal(saved.health, 'problem'); assert.equal(saved.truncated, true);
  assert.equal(saved.frames[0].failed[0].label, 'Location');
  assert.ok(JSON.stringify(saved).length < 50000);
});
