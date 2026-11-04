// A capsule is a user-reviewed snapshot. Its digest detects accidental changes;
// it does not certify that an employer received these materials.
export const CAPSULE_SCHEMA = 'jobpilot.capsule/v1';
const MAX_PDF = 10 * 1024 * 1024;
const MAX_RECEIPT = 5 * 1024 * 1024;
const TYPES = new Set(['application/pdf', 'image/png', 'image/jpeg', 'text/plain', 'message/rfc822']);

export function text(value, max = 1000) {
  if (value == null) return '';
  if (typeof value !== 'string' || value.length > max) throw new Error(`Expected text of at most ${max} characters.`);
  return value.trim();
}
function exactText(value, max) {
  text(value, max);
  return value ?? '';
}
export function publicUrl(value) {
  let url;
  try { url = new URL(text(value, 5000)); } catch { throw new Error('Enter a complete job URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Use an http or https URL without credentials.');
  return url.href;
}
export async function digest(value) {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
}
function timestamp(value, label) {
  if (typeof value !== 'string' || !value || Number.isNaN(Date.parse(value))) throw new Error(`Enter a valid ${label}.`);
  return new Date(value).toISOString();
}
function array(value, limit, label) {
  if (!Array.isArray(value) || value.length > limit) throw new Error(`${label} must have at most ${limit} entries.`);
  return value;
}
export async function attachment(value, {resume = false} = {}) {
  if (!value || typeof value !== 'object') throw new Error('Choose an attachment.');
  const name = text(value.name, 500), type = text(value.type, 100);
  if (!name || !TYPES.has(type) || (resume && type !== 'application/pdf')) throw new Error(resume ? 'Choose a PDF résumé.' : 'Use a PNG, JPG, PDF, TXT, or EML receipt.');
  const limit = resume ? MAX_PDF : MAX_RECEIPT;
  if (typeof value.dataUrl !== 'string' || value.dataUrl.length > Math.ceil(limit / 3) * 4 + 200) throw new Error('Attachment is too large.');
  const match = /^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$/.exec(value.dataUrl);
  if (!match || match[1] !== type) throw new Error('Attachment encoding does not match its type.');
  let binary;
  try { binary = atob(match[2]); } catch { throw new Error('Invalid attachment encoding.'); }
  if (!binary.length || binary.length > limit || value.size !== binary.length) throw new Error('Attachment size is invalid or exceeds its limit.');
  if (type === 'application/pdf' && !binary.startsWith('%PDF-')) throw new Error('This file is not a PDF.');
  if (type === 'image/png' && !binary.startsWith('\x89PNG\r\n\x1a\n')) throw new Error('This file is not a PNG.');
  if (type === 'image/jpeg' && !binary.startsWith('\xff\xd8\xff')) throw new Error('This file is not a JPEG.');
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  const sha256 = await digest(bytes);
  if (value.sha256 && value.sha256 !== sha256) throw new Error('Attachment checksum does not match.');
  return {name, type, size: bytes.length, sha256, dataUrl: value.dataUrl};
}
export async function fileAttachment(file, resume = false) {
  if (!file || file.size > (resume ? MAX_PDF : MAX_RECEIPT)) throw new Error(resume ? 'Choose a PDF up to 10 MiB.' : 'Choose a receipt up to 5 MiB.');
  const extension = file.name.split('.').pop().toLowerCase();
  const type = ({pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', txt: 'text/plain', eml: 'message/rfc822'})[extension];
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return attachment({name: file.name, type, size: bytes.length, dataUrl: `data:${type};base64,${btoa(binary)}`}, {resume});
}
async function payload(value, id, sealedAt) {
  if (!value || typeof value !== 'object') throw new Error('Invalid capsule.');
  const job = value.application;
  if (!job || !text(job.company) || !text(job.title)) throw new Error('Enter the company and role.');
  const answers = array(value.answers ?? [], 100, 'Answers').map(answer => {
    const question = exactText(answer.question, 5000), response = exactText(answer.answer, 20000);
    if (!question.trim() || !response.trim()) throw new Error('Each saved question needs its final answer.');
    return {question, answer: response};
  });
  const stories = array(value.stories ?? [], 100, 'Stories').map(story => ({
    id: text(story.id, 100), revision: Number.isSafeInteger(story.revision) ? story.revision : 1,
    title: text(story.title), summary: text(story.summary, 12000), source: text(story.source, 5000),
    details: array(story.details ?? [], 100, 'Story details').map(v => text(v, 4000)),
    truth_boundaries: array(story.truth_boundaries ?? [], 100, 'Truth boundaries').map(v => text(v, 4000)),
    reviewedAt: story.reviewedAt ? timestamp(story.reviewedAt, 'story review date') : '',
  }));
  return {schema: CAPSULE_SCHEMA, id, sealedAt, application: {company: text(job.company), title: text(job.title), url: publicUrl(job.url)},
    submittedAt: timestamp(value.submittedAt, 'submission date'), submissionNote: exactText(value.submissionNote, 10000),
    postingText: exactText(value.postingText, 120000), answers, stories,
    resume: value.resume ? await attachment(value.resume, {resume: true}) : null,
    receipt: value.receipt ? await attachment(value.receipt) : null,
    recordType: 'user_recorded_submission'};
}
export async function sealCapsule(value) {
  if (value.confirmed !== true) throw new Error('Review the materials and confirm that you submitted this application before sealing.');
  const body = await payload(value, crypto.randomUUID(), new Date().toISOString());
  return {...body, integrity: {algorithm: 'SHA-256', sha256: await digest(JSON.stringify(body))}};
}
export async function validateCapsule(value) {
  if (value?.schema !== CAPSULE_SCHEMA || !/^[A-Za-z0-9_-]{1,100}$/.test(value.id ?? '')) throw new Error('Unsupported capsule format.');
  const body = await payload(value, value.id, timestamp(value.sealedAt, 'seal date'));
  if (value.integrity?.algorithm !== 'SHA-256' || value.integrity.sha256 !== await digest(JSON.stringify(body))) throw new Error('Capsule checksum does not match. Nothing was imported.');
  return {...body, integrity: {algorithm: 'SHA-256', sha256: value.integrity.sha256}};
}
export function capsuleSummary(value) {
  return {id: value.id, sealedAt: value.sealedAt, submittedAt: value.submittedAt, application: value.application,
    resumeName: value.resume?.name || '', receiptName: value.receipt?.name || '', answerCount: value.answers.length,
    sha256: value.integrity.sha256};
}
