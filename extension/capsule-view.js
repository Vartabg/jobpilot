import {fileAttachment} from './capsule.js';
import {el, field, button, option, dated, guard, downloadAttachment} from './workspace-ui.js';

export function mountCapsules(root, {call, notify, download}) {
  let context = {applications: [], resumes: [], stories: []}, pending = null;
  const run = action => guard(root, notify, action);
  const form = el('form'), existing = field('Start from a tracked application', {tag: 'select'});
  const company = field('Company', {required: true}), title = field('Role', {required: true});
  const url = field('Job URL', {type: 'url', required: true, maxLength: 5000});
  const submitted = field('When you submitted', {type: 'datetime-local', required: true});
  const resume = field('Saved résumé used for this application', {tag: 'select'});
  const upload = field('Or choose the exact résumé PDF (up to 10 MiB)', {type: 'file'});
  upload.input.accept = '.pdf,application/pdf';
  const posting = field('Job description as it appeared when you applied', {tag: 'textarea', maxLength: 120000, rows: 6});
  const notes = field('Submission notes', {tag: 'textarea', maxLength: 10000});
  const receipt = field('Confirmation receipt (PNG, JPG, PDF, TXT, or EML; up to 5 MiB)', {type: 'file'});
  receipt.input.accept = '.png,.jpg,.jpeg,.pdf,.txt,.eml';
  const storyChoices = el('fieldset'), legend = el('legend', 'Reviewed career stories to preserve with this record');
  storyChoices.append(legend);
  const answers = el('div'), review = el('section', undefined, 'card'), list = el('div');
  review.hidden = true;
  const importFile = field('Import an exported capsule JSON', {type: 'file'}); importFile.input.accept = '.json,application/json';
  const importPreview = el('div', undefined, 'import-preview');
  let incoming = null;
  function invalidate() { pending = null; review.hidden = true; }
  form.addEventListener('input', invalidate); form.addEventListener('change', invalidate);
  const pair = el('div', undefined, 'two-column'); pair.append(company.wrap, title.wrap);
  const preview = el('button', 'Review capsule', 'primary'); preview.type = 'submit';
  form.append(el('h3', 'Preserve a completed application'), existing.wrap, pair, url.wrap, submitted.wrap,
    resume.wrap, upload.wrap, posting.wrap, el('h3', 'Final questions and answers'),
    el('p', 'Paste the final wording you actually submitted. Blank answers are not included.'), answers,
    button('Add a question and answer', () => { addAnswer(); invalidate(); }), storyChoices, notes.wrap, receipt.wrap,
    el('p', 'Missing materials stay visibly missing. Sealing records your account of a submission; it does not contact the employer or verify delivery.', 'fine-print'), preview);
  root.replaceChildren(el('h2', 'Application time capsules'), el('p', 'Keep the exact materials for each application, ready to revisit before an interview. Sealed records keep independent copies of attached files.'),
    form, review, el('h3', 'Your sealed records'), button('Refresh sealed records', () => void run(refresh)), list, importFile.wrap, importPreview);
  function addAnswer(question = '', answer = '') {
    const row = el('div', undefined, 'answer-row');
    const q = field('Question', {maxLength: 5000}), a = field('Final answer', {tag: 'textarea', maxLength: 20000});
    q.input.value = question; a.input.value = answer; q.input.dataset.question = ''; a.input.dataset.answer = '';
    row.append(q.wrap, a.wrap, button('Remove this answer', () => { row.remove(); invalidate(); })); answers.append(row);
  }
  existing.input.addEventListener('change', () => {
    const app = context.applications.find(v => v.id === existing.input.value);
    if (!app) return;
    company.input.value = app.company; title.input.value = app.title; url.input.value = app.url;
    notes.input.value = app.notes || ''; resume.input.value = app.resume_id || '';
    if (app.submittedAt) submitted.input.value = localDate(app.submittedAt);
  });
  function localDate(value) {
    const date = new Date(value); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  submitted.input.value = localDate(new Date());
  form.addEventListener('submit', event => {
    event.preventDefault();
    void run(async () => {
      const finalAnswers = [...answers.querySelectorAll('.answer-row')].map(row => ({question: row.querySelector('[data-question]').value,
        answer: row.querySelector('[data-answer]').value})).filter(row => row.question.trim() || row.answer.trim());
      if (finalAnswers.some(row => !row.question.trim() || !row.answer.trim())) throw new Error('Provide both the question and its final answer, or remove that row.');
      const storyIds = [...storyChoices.querySelectorAll('input:checked')].map(v => v.value);
      const storyVersions = Object.fromEntries(context.stories.filter(v => storyIds.includes(v.id)).map(v => [v.id, `${v.revision}:${v.reviewedAt}`]));
      pending = {application: {company: company.input.value, title: title.input.value, url: url.input.value},
        submittedAt: new Date(submitted.input.value).toISOString(), postingText: posting.input.value, answers: finalAnswers,
        storyIds, storyVersions, submissionNote: notes.input.value,
        resumeId: upload.input.files[0] ? '' : resume.input.value,
        resume: upload.input.files[0] ? await fileAttachment(upload.input.files[0], true) : null,
        receipt: receipt.input.files[0] ? await fileAttachment(receipt.input.files[0]) : null};
      renderReview();
    });
  });
  function renderReview() {
    review.replaceChildren(el('h3', 'Review before sealing'), el('p', `${pending.application.company} · ${pending.application.title}`),
      el('p', `Submission recorded for ${dated(pending.submittedAt)}`),
      el('p', `Résumé: ${pending.resume?.name || context.resumes.find(v => v.id === pending.resumeId)?.name || 'Not included'}`),
      el('p', `Receipt: ${pending.receipt?.name || 'Not included'}`),
      el('p', `Career stories: ${pending.storyIds.length}`));
    for (const id of pending.storyIds) {
      const story = context.stories.find(v => v.id === id);
      const detail = el('details'); detail.append(el('summary', story.title), el('pre', story.summary), el('pre', story.truth_boundaries.join('\n'))); review.append(detail);
    }
    const detail = el('details'); detail.append(el('summary', pending.postingText ? 'Review saved job description' : 'No job description included'), el('pre', pending.postingText)); review.append(detail);
    for (const answer of pending.answers) review.append(el('h3', answer.question), el('pre', answer.answer));
    if (pending.submissionNote) review.append(el('pre', pending.submissionNote));
    const confirm = field('I submitted this application and reviewed these saved materials.', {type: 'checkbox'});
    const seal = button('Seal this application record', () => void run(async () => {
      if (!confirm.input.checked) throw new Error('Confirm your submission and review before sealing.');
      const result = await call('sealCapsule', {...pending, confirmed: true});
      invalidate(); form.reset(); answers.replaceChildren(); submitted.input.value = localDate(new Date());
      await refresh(); notify(`Sealed ${result.application.company}. The saved record will not change when you edit your résumé or stories.`);
    }), 'primary');
    review.append(confirm.wrap, el('div', undefined, 'actions'));
    review.lastChild.append(seal, button('Go back and edit', () => { invalidate(); company.input.focus(); }));
    review.hidden = false; review.scrollIntoView({block: 'nearest'}); confirm.input.focus();
  }
  async function showCapsule(summary, card) {
    const capsule = await call('getCapsule', summary.id);
    const detail = el('div'); detail.append(el('p', 'User-recorded submission. A receipt attachment is saved evidence, not automatic verification of employer delivery.', 'fine-print'),
      el('p', capsule.submissionNote || 'No submission notes.'), el('h3', 'Saved job description'), el('pre', capsule.postingText || 'Not included'));
    for (const answer of capsule.answers) detail.append(el('h3', answer.question), el('pre', answer.answer));
    for (const story of capsule.stories) detail.append(el('h3', `Career story: ${story.title} · revision ${story.revision}`), el('pre', story.summary), el('pre', story.truth_boundaries.join('\n')));
    const actions = el('div', undefined, 'actions');
    if (capsule.resume) actions.append(button('Download exact résumé', () => downloadAttachment(capsule.resume)));
    if (capsule.receipt) actions.append(button('Download saved receipt', () => downloadAttachment(capsule.receipt)));
    actions.append(button('Export complete capsule', () => download(JSON.stringify(capsule, null, 2), `jobpilot-capsule-${capsule.id}.json`)));
    detail.append(actions, el('p', 'SHA-256 integrity checksum'), el('code', capsule.integrity.sha256)); card.append(detail);
  }
  async function refresh() {
    context = await call('getContext');
    existing.input.replaceChildren(); option(existing.input, '', 'Enter details yourself');
    for (const app of context.applications) option(existing.input, app.id, `${app.company} · ${app.title} (${app.status})`);
    const selectedResume = resume.input.value || context.selectedResumeId;
    resume.input.replaceChildren(); option(resume.input, '', 'No saved résumé selected');
    for (const file of context.resumes) if (!file.missingFile) option(resume.input, file.id, file.label || file.name);
    resume.input.value = selectedResume || '';
    const checked = new Set([...storyChoices.querySelectorAll('input:checked')].map(v => v.value));
    storyChoices.replaceChildren(legend);
    for (const story of context.stories.filter(v => v.reviewedAt)) {
      const check = field(`${story.title} · revision ${story.revision}`, {type: 'checkbox'}); check.input.value = story.id; check.input.checked = checked.has(story.id); storyChoices.append(check.wrap);
    }
    if (!context.stories.some(v => v.reviewedAt)) storyChoices.append(el('p', 'Review a story in Career memory to include it here.', 'fine-print'));
    const capsules = await call('listCapsules'); list.replaceChildren();
    if (!capsules.length) list.append(el('p', 'Your first sealed application will appear here.', 'empty'));
    for (const capsule of capsules) {
      const card = el('article', undefined, 'card');
      const details = button('Open saved materials', () => void run(async () => { await showCapsule(capsule, card); details.remove(); }));
      card.append(el('h3', `${capsule.application.company} · ${capsule.application.title}`), el('p', dated(capsule.submittedAt), 'badge'),
        el('p', `Résumé: ${capsule.resumeName || 'Not included'}\nReceipt: ${capsule.receiptName || 'Not included'}\nFinal answers: ${capsule.answerCount}`), details); list.append(card);
    }
  }
  importFile.input.addEventListener('change', () => void run(async () => {
    incoming = null; importPreview.replaceChildren();
    const file = importFile.input.files[0]; if (!file) return;
    if (file.size > 24 * 1024 * 1024) throw new Error('Capsule imports must be 24 MiB or smaller.');
    const {validateCapsule} = await import('./capsule.js'); incoming = await validateCapsule(JSON.parse(await file.text()));
    importPreview.append(el('h3', 'Review capsule import'), el('p', `${incoming.application.company} · ${incoming.application.title}\n${dated(incoming.submittedAt)}\nChecksum and attachments validated.`),
      button('Import this capsule', () => void run(async () => {
        const result = await call('importCapsule', incoming); incoming = null; importPreview.replaceChildren(); importFile.input.value = ''; await refresh();
        notify(result.added ? 'Capsule restored with its original materials.' : 'That identical capsule is already saved.');
      })));
  }));
  return {refresh};
}
