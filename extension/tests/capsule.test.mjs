import assert from 'node:assert/strict';
import {test} from 'node:test';
import {attachment, sealCapsule, validateCapsule} from '../capsule.js';

const pdf = Buffer.from('%PDF-1.4\nsynthetic exact resume\n%%EOF');
const file = {name: 'Exact resume.pdf', type: 'application/pdf', size: pdf.length, dataUrl: `data:application/pdf;base64,${pdf.toString('base64')}`};
const input = (extra = {}) => ({confirmed: true, application: {company: 'Fixture Co', title: 'Support engineer', url: 'https://jobs.ashbyhq.com/fixture/req-1'},
  submittedAt: '2026-10-15T14:30:00Z', postingText: '  Exact posting\nSecond line.  ', submissionNote: '  Human recorded.  ',
  answers: [{question: '  Describe your contribution.  ', answer: '  I isolated the fault.\n\nThen explained it.  '}], resume: {...file}, ...extra});

test('sealing requires explicit human confirmation and leaves missing materials visibly absent', async () => {
  for (const confirmed of [undefined, false, 'true', 1]) await assert.rejects(sealCapsule(input({confirmed})), /confirm/i);
  const capsule = await sealCapsule(input({resume: null, answers: [], postingText: ''}));
  assert.equal(capsule.recordType, 'user_recorded_submission');
  assert.equal(capsule.resume, null); assert.equal(capsule.receipt, null);
  assert.deepEqual(capsule.answers, []); assert.equal(capsule.postingText, '');
});

test('exact questions, answers, posting and notes retain whitespace and file bytes are independent', async () => {
  const original = input(); const capsule = await sealCapsule(original);
  assert.deepEqual(capsule.answers, original.answers);
  assert.equal(capsule.postingText, original.postingText); assert.equal(capsule.submissionNote, original.submissionNote);
  assert.equal(Buffer.from(capsule.resume.dataUrl.split(',')[1], 'base64').compare(pdf), 0);
  original.resume.dataUrl = 'changed'; original.answers[0].answer = 'changed';
  assert.equal(capsule.resume.dataUrl, file.dataUrl); assert.notEqual(capsule.answers[0].answer, 'changed');
  assert.deepEqual(await validateCapsule(capsule), capsule);
});

test('tampered answers, checksums and attachment bytes fail validation', async () => {
  const capsule = await sealCapsule(input());
  for (const mutate of [value => { value.answers[0].answer += ' fabricated'; },
    value => { value.integrity.sha256 = '0'.repeat(64); }, value => { value.resume.dataUrl = file.dataUrl.replace('JVBER', 'AVBER'); }]) {
    const copy = structuredClone(capsule); mutate(copy);
    await assert.rejects(validateCapsule(copy), /checksum|PDF/i);
  }
  const copy = structuredClone(capsule); copy.resume.size++;
  await assert.rejects(validateCapsule(copy), /size/i);
});

test('invalid attachments, credentials, partial answers and oversize text are rejected', async () => {
  await assert.rejects(attachment({...file, type: 'text/html'}), /receipt/i);
  await assert.rejects(attachment({...file, dataUrl: 'data:application/pdf;base64,not!base64'}), /encoding/i);
  await assert.rejects(sealCapsule(input({application: {company: 'Fixture', title: 'Role', url: 'https://user:pass@example.org/job'}})), /credentials/i);
  await assert.rejects(sealCapsule(input({answers: [{question: 'Why?', answer: '   '}]})), /final answer/i);
  await assert.rejects(sealCapsule(input({postingText: 'x'.repeat(120001)})), /120000/);
  await assert.rejects(sealCapsule(input({submittedAt: 'invalid'})), /submission date/i);
});

test('a sealed story is copied with its truth boundaries, without becoming a verified qualification', async () => {
  const stories = [{id: 'field', title: 'Field repair', summary: 'Diagnosed one fault.', details: ['My contribution only.'],
    truth_boundaries: ['Do not invent a team size.'], revision: 2, reviewedAt: '2026-10-14T15:00:00Z'}];
  const capsule = await sealCapsule(input({stories}));
  stories[0].summary = 'Updated later'; stories[0].truth_boundaries.push('Added later');
  assert.equal(capsule.stories[0].summary, 'Diagnosed one fault.');
  assert.deepEqual(capsule.stories[0].truth_boundaries, ['Do not invent a team size.']);
  assert.equal(capsule.stories[0].revision, 2);
  assert.equal(capsule.recordType, 'user_recorded_submission');
});
