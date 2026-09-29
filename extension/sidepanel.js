import {
  getProfile, saveProfile, listResumes, addResume, getResume, deleteResume,
  listApplications, saveApplication, updateApplication, findApplication,
  exportData, importData, normalizeJobUrl,
} from './model.js';
import { captureCurrentJob, runCurrentAction } from './actions.js';

const UI_KEY = 'jobpilot_ui_v1';
const STATUS_LABELS = {
  started: 'Started', submitted: 'Submitted', interview: 'Interview',
  rejected: 'Rejected', offer: 'Offer', withdrawn: 'Withdrawn',
};
const PROFILE_FIELDS = [
  'first_name', 'last_name', 'email', 'phone', 'city', 'state', 'postal_code',
  'country', 'linkedin_url', 'github_url', 'portfolio_url', 'current_title',
];
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const state = {
  profile: {}, resumes: [], applications: [], selectedResumeId: '',
  job: { title: '', company: '', url: '', notes: '', provider: '', supported: null },
  report: null, tab: 'apply', busy: false, pendingImport: null, dirtyProfile: false,
};
let draftTimer;
let duplicateGeneration = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function showNotice(message, kind = 'success') {
  const notice = $('#notice');
  notice.textContent = message;
  notice.className = `notice${kind === 'success' ? '' : ` ${kind}`}`;
  notice.hidden = false;
}

function friendlyError(error) {
  return typeof error?.message === 'string' && error.message.trim()
    ? error.message : 'The action could not be completed. Please try again.';
}

function validWebUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function hostOf(value) {
  try { return new URL(value).hostname; } catch { return ''; }
}

function sameApplicationUrl(first, second) {
  try { return normalizeJobUrl(first).identity === normalizeJobUrl(second).identity; }
  catch { return false; }
}

function readableDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? ''
    : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function readableTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? ''
    : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
}

function byteSize(size) {
  const bytes = Number(size) || 0;
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function profileDirty() {
  state.dirtyProfile = true;
  $('#profile-save-state').textContent = 'You have unsaved profile changes.';
}

async function persistUi() {
  await chrome.storage.local.set({
    [UI_KEY]: {
      tab: state.tab, job: state.job, selectedResumeId: state.selectedResumeId,
      report: state.report,
    },
  });
}

function queuePersistUi() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    persistUi().catch(error => showNotice(`Could not save this workspace: ${friendlyError(error)}`, 'error'));
  }, 250);
}

function switchTab(tab, moveFocus = false) {
  if (!['apply', 'profile', 'applications'].includes(tab)) return;
  state.tab = tab;
  for (const button of $$('[data-tab]')) {
    const selected = button.dataset.tab === tab;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(`#panel-${button.dataset.tab}`).hidden = !selected;
    if (selected && moveFocus) button.focus();
  }
  queuePersistUi();
}

function setBusy(busy) {
  state.busy = busy;
  document.body.setAttribute('aria-busy', String(busy));
  for (const button of [
    $('#capture-job'), $('#inspect-form'), $('#fill-details'), $('#attach-resume'),
    $('#save-started'), $('#save-submitted'), $('#save-profile'), $('#export-data'),
    $('#confirm-import'), $('#refresh-applications'),
  ]) button.disabled = busy;
  $('#resume-upload').disabled = busy;
  $('#import-data').disabled = busy;
  for (const control of $$('#application-list select, #resume-library button')) control.disabled = busy;
  if (!busy) updateActionButtons();
}

async function perform(task) {
  if (state.busy) return;
  setBusy(true);
  try { await task(); }
  catch (error) { showNotice(friendlyError(error), 'error'); }
  finally { setBusy(false); }
}

function updateActionButtons() {
  const resume = state.resumes.find(item => item.id === state.selectedResumeId);
  $('#attach-resume').disabled = state.busy || !resume || Boolean(resume.missingFile);
}

function readJob() {
  return {
    ...state.job,
    title: $('#job-title').value.trim(), company: $('#job-company').value.trim(),
    url: $('#job-url').value.trim(), notes: $('#job-notes').value.trim(),
  };
}

function renderJob() {
  for (const key of ['title', 'company', 'url', 'notes']) $(`#job-${key}`).value = state.job[key] || '';
  renderPortalStatus();
  checkDuplicate();
}

function renderPortalStatus() {
  const status = $('#portal-status');
  status.replaceChildren();
  const dot = element('span', 'status-dot');
  dot.setAttribute('aria-hidden', 'true');
  const captured = Boolean(state.job.capturedAt);
  let message = 'Capture the tab to identify this page.';
  if (captured && state.job.supported === true) {
    dot.classList.add('supported');
    message = `${state.job.provider || 'Supported portal'} · Autofill available on this captured page`;
  } else if (captured && state.job.supported === false) {
    dot.classList.add('unsupported');
    message = 'Autofill unavailable here. You can still save this job and use your profile.';
  } else if (state.job.url) {
    message = 'Job draft saved. Capture the tab to check autofill support.';
  }
  status.append(dot, element('span', '', message));
}

async function checkDuplicate() {
  const generation = ++duplicateGeneration;
  const notice = $('#duplicate-notice');
  if (!validWebUrl(state.job.url)) { notice.hidden = true; return; }
  try {
    const match = await findApplication(state.job.url);
    if (generation !== duplicateGeneration) return;
    notice.hidden = !match;
    if (match) {
      notice.replaceChildren(element('span', '', `Already saved · ${STATUS_LABELS[match.status] || match.status}. This job has one shared record.`));
      const open = element('button', 'text-button', 'View applications');
      open.type = 'button';
      open.addEventListener('click', () => switchTab('applications'));
      notice.append(document.createTextNode(' '), open);
    }
  } catch (error) {
    if (generation === duplicateGeneration) { notice.hidden = true; }
  }
}

function renderProfileSummary() {
  const container = $('#profile-summary');
  const name = [state.profile.first_name, state.profile.last_name].filter(Boolean).join(' ');
  const hasProfile = Boolean(name || state.profile.email || state.profile.work_history?.length);
  const text = hasProfile
    ? [name || 'Saved profile', state.profile.email || ''].filter(Boolean).join(' · ')
    : 'Add your profile to fill application fields.';
  container.replaceChildren(element('span', '', text));
  const edit = element('button', 'text-button', hasProfile ? 'Edit' : 'Add profile');
  edit.type = 'button';
  edit.addEventListener('click', () => switchTab('profile'));
  container.append(edit);
}

function labeledInput(label, key, value = '', type = 'text') {
  const wrapper = element('label', '', label);
  const input = element('input');
  input.type = type;
  input.dataset.field = key;
  input.value = value || '';
  wrapper.append(input);
  return wrapper;
}

function emptyHistory(container, text) {
  container.replaceChildren(element('p', 'empty-state', text));
}

function workEntry(row = {}, index = 0) {
  const card = element('div', 'history-entry');
  card.dataset.history = 'work';
  card.dataset.id = row.id || crypto.randomUUID();
  const header = element('div', 'entry-heading');
  header.append(element('h3', '', `Position ${index + 1}`));
  const remove = element('button', 'text-button danger', 'Remove');
  remove.type = 'button';
  remove.setAttribute('aria-label', `Remove position ${index + 1}`);
  remove.addEventListener('click', () => {
    card.remove(); profileDirty(); renumberHistory('work');
    if (!$('#work-history').children.length) emptyHistory($('#work-history'), 'No positions added yet.');
  });
  header.append(remove);
  card.append(header, labeledInput('Company', 'company', row.company), labeledInput('Title', 'title', row.title));
  const dates = element('div', 'field-grid');
  const start = labeledInput('Start date', 'start_date', row.start_date);
  const end = labeledInput('End date', 'end_date', row.end_date);
  $('input', start).placeholder = 'YYYY-MM';
  $('input', end).placeholder = 'YYYY-MM';
  $('input', start).setAttribute('aria-label', `Position ${index + 1} start date, year and month`);
  $('input', end).setAttribute('aria-label', `Position ${index + 1} end date, year and month`);
  $('input', end).disabled = Boolean(row.current);
  dates.append(start, end);
  const current = element('label', 'check-label');
  const checkbox = element('input');
  checkbox.type = 'checkbox'; checkbox.dataset.field = 'current'; checkbox.checked = Boolean(row.current);
  checkbox.addEventListener('change', () => {
    const input = $('[data-field="end_date"]', card);
    input.disabled = checkbox.checked;
  });
  current.append(checkbox, element('span', '', 'I currently work here'));
  const description = element('label', '', 'Responsibilities and accomplishments');
  const textarea = element('textarea');
  textarea.rows = 3; textarea.dataset.field = 'description'; textarea.value = row.description || '';
  description.append(textarea);
  card.append(dates, current, description);
  card.addEventListener('input', profileDirty);
  return card;
}

function educationEntry(row = {}, index = 0) {
  const card = element('div', 'history-entry');
  card.dataset.history = 'education';
  card.dataset.id = row.id || crypto.randomUUID();
  const header = element('div', 'entry-heading');
  header.append(element('h3', '', `Education ${index + 1}`));
  const remove = element('button', 'text-button danger', 'Remove');
  remove.type = 'button';
  remove.setAttribute('aria-label', `Remove education ${index + 1}`);
  remove.addEventListener('click', () => {
    card.remove(); profileDirty(); renumberHistory('education');
    if (!$('#education-history').children.length) emptyHistory($('#education-history'), 'No education added yet.');
  });
  header.append(remove);
  card.append(header, labeledInput('School / institution', 'school', row.school), labeledInput('Degree / qualification', 'degree', row.degree), labeledInput('Field of study', 'field', row.field || row.field_of_study));
  const dates = element('div', 'field-grid');
  const start = labeledInput('Start date', 'start_date', row.start_date);
  const end = labeledInput('End / expected date', 'end_date', row.end_date);
  $('input', start).placeholder = 'YYYY-MM';
  $('input', end).placeholder = 'YYYY-MM';
  dates.append(start, end);
  card.append(dates);
  card.addEventListener('input', profileDirty);
  return card;
}

function renumberHistory(kind) {
  const container = $(kind === 'work' ? '#work-history' : '#education-history');
  const entries = $$('[data-history]', container);
  entries.forEach((entry, index) => {
    const label = kind === 'work' ? 'Position' : 'Education';
    $('h3', entry).textContent = `${label} ${index + 1}`;
    $('button', entry).setAttribute('aria-label', `Remove ${label.toLowerCase()} ${index + 1}`);
  });
}

function renderProfile() {
  const form = $('#profile-form');
  for (const key of PROFILE_FIELDS) form.elements.namedItem(key).value = state.profile[key] || '';
  for (const key of ['work_authorized', 'requires_sponsorship']) {
    form.elements.namedItem(key).value = state.profile[key] === true ? 'yes' : state.profile[key] === false ? 'no' : '';
  }
  const work = $('#work-history');
  const education = $('#education-history');
  work.replaceChildren(); education.replaceChildren();
  const workRows = Array.isArray(state.profile.work_history) ? state.profile.work_history : [];
  const educationRows = Array.isArray(state.profile.education) ? state.profile.education : [];
  workRows.forEach((row, index) => work.append(workEntry(row, index)));
  educationRows.forEach((row, index) => education.append(educationEntry(row, index)));
  if (!workRows.length) emptyHistory(work, 'No positions added yet.');
  if (!educationRows.length) emptyHistory(education, 'No education added yet.');
  state.dirtyProfile = false;
  $('#profile-save-state').textContent = 'Changes save when you choose Save profile.';
  renderProfileSummary();
}

function readHistory(selector) {
  return $$('[data-history]', $(selector)).map(entry => {
    const row = { id: entry.dataset.id };
    for (const input of $$('[data-field]', entry)) {
      row[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.value.trim();
    }
    if (row.current) row.end_date = '';
    return row;
  });
}

function readProfile() {
  const result = { ...state.profile };
  for (const key of PROFILE_FIELDS) result[key] = $('#profile-form').elements.namedItem(key).value.trim();
  for (const key of ['work_authorized', 'requires_sponsorship']) {
    const value = $('#profile-form').elements.namedItem(key).value;
    result[key] = value === 'yes' ? true : value === 'no' ? false : null;
  }
  result.work_history = readHistory('#work-history');
  result.education = readHistory('#education-history');
  result.selected_resume_id = state.selectedResumeId;
  return result;
}

function renderResumes() {
  const select = $('#apply-resume');
  select.replaceChildren();
  const emptyOption = element('option', '', state.resumes.length ? 'Choose a résumé' : 'Add a PDF in Profile first');
  emptyOption.value = '';
  select.append(emptyOption);
  for (const resume of state.resumes) {
    const option = element('option', '', `${resume.label || resume.name}${resume.missingFile ? ' · PDF needed' : ''}`);
    option.value = resume.id;
    option.disabled = Boolean(resume.missingFile);
    select.append(option);
  }
  if (!state.resumes.some(resume => resume.id === state.selectedResumeId && !resume.missingFile)) state.selectedResumeId = '';
  select.value = state.selectedResumeId;
  const library = $('#resume-library');
  library.replaceChildren();
  if (!state.resumes.length) library.append(element('p', 'empty-state', 'No résumés yet. Add your first PDF above.'));
  for (const resume of state.resumes) {
    const entry = element('div', 'resume-entry');
    entry.append(element('span', 'pdf-mark', 'PDF'));
    const info = element('div', 'resume-info');
    info.append(element('div', 'resume-name', resume.label || resume.name));
    if (resume.label && resume.label !== resume.name) info.append(element('p', '', resume.name));
    if (resume.missingFile) info.append(element('p', 'missing-file', 'Name restored from backup · add the PDF to use it'));
    else info.append(element('p', '', `${byteSize(resume.size)}${resume.createdAt ? ` · ${readableDate(resume.createdAt)}` : ''}`));
    const remove = element('button', 'text-button danger', 'Delete');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Delete résumé ${resume.label || resume.name}`);
    let confirmed = false;
    remove.addEventListener('click', () => {
      if (!confirmed) {
        confirmed = true; remove.textContent = 'Delete?';
        setTimeout(() => { if (remove.isConnected) { confirmed = false; remove.textContent = 'Delete'; } }, 5000);
        return;
      }
      perform(async () => {
        await deleteResume(resume.id);
        state.resumes = await listResumes();
        renderResumes(); await persistUi();
        showNotice('Résumé removed from your library.');
      });
    });
    entry.append(info, remove);
    library.append(entry);
  }
  updateActionButtons();
}

function safeReportFrames(report) {
  const frames = Array.isArray(report?.frames) ? report.frames : [];
  return frames.map(frame => frame?.result && typeof frame.result === 'object' ? { ...frame.result, frameId: frame.frameId } : frame)
    .filter(frame => frame && typeof frame === 'object');
}

function reportGroup(container, heading, rows, formatter, open = false) {
  if (!rows.length) return;
  const details = element('details');
  details.open = open;
  details.append(element('summary', '', `${heading} (${rows.length})`));
  const list = element('ul');
  for (const row of rows) list.append(element('li', '', formatter(row)));
  details.append(list);
  container.append(details);
}

function renderReport() {
  const card = $('#report-card');
  card.hidden = !state.report;
  if (!state.report) return;
  const report = state.report;
  const frames = safeReportFrames(report);
  const groups = { filled: [], skipped: [], failed: [], required: [] };
  for (const frame of frames) {
    for (const key of Object.keys(groups)) {
      if (Array.isArray(frame[key])) groups[key].push(...frame[key].map(row => ({ ...row, frameId: frame.frameId })));
    }
    if (frame.error) groups.failed.push({ label: `Frame ${frame.frameId ?? 'unknown'}`, reason: String(frame.error) });
  }
  const action = report.uiAction || frames[0]?.action || 'inspect';
  $('#report-heading').textContent = action === 'resume' ? 'Résumé result' : action === 'details' ? 'Autofill results' : 'Field inspection';
  $('#report-context').textContent = [hostOf(report.pageUrl || frames[0]?.pageUrl), readableTime(report.checkedAt)].filter(Boolean).join(' · ');
  let summary;
  if (action === 'inspect') {
    summary = groups.required.length
      ? `${groups.required.length} visible required ${groups.required.length === 1 ? 'field needs' : 'fields need'} attention.`
      : frames.length ? 'No empty visible required fields were detected.' : 'No form results were returned.';
  } else if (action === 'details') {
    summary = `${groups.filled.length} ${groups.filled.length === 1 ? 'field' : 'fields'} filled · ${groups.skipped.length} skipped · ${groups.failed.length} failed.`;
  } else {
    summary = 'Review the résumé result below and the attachment shown on the application page.';
  }
  $('#report-summary').textContent = summary;
  const upload = $('#report-upload');
  upload.replaceChildren(); upload.hidden = true; upload.className = 'inline-note';
  if (action === 'resume') {
    const resumeResults = frames.map(frame => frame.resume).filter(item => item && typeof item === 'object');
    upload.hidden = false;
    if (!resumeResults.length) upload.textContent = 'No upload result was returned. Check the application page before continuing.';
    else {
      const lines = resumeResults.map(item => {
        const prefix = {
          accepted: 'Page confirmed the attachment', prepared: 'File added; page acceptance is unconfirmed',
          failed: 'Attachment failed', 'already-attached': 'Existing attachment preserved',
          'no-target': 'No résumé upload field found',
        }[item.status] || 'Attachment needs review';
        return `${prefix}${item.filename ? ` (${item.filename})` : ''}${item.message ? ` · ${item.message}` : ''}`;
      });
      upload.textContent = lines.join('\n');
      if (resumeResults.every(item => item.status === 'accepted')) upload.classList.add('good');
    }
  }
  const container = $('#report-details');
  container.replaceChildren();
  reportGroup(container, 'Filled', groups.filled, row => row.label || row.key || 'Unlabeled field');
  reportGroup(container, 'Still required', groups.required, row => `${row.label || 'Unlabeled field'}${row.type ? ` · ${row.type}` : ''}`, true);
  reportGroup(container, 'Skipped', groups.skipped, row => `${row.label || 'Unlabeled field'} · ${row.reason || 'Not filled'}`);
  reportGroup(container, 'Failed', groups.failed, row => `${row.label || 'Unlabeled field'} · ${row.reason || 'Could not fill'}`, true);
  if (!frames.length) container.append(element('p', 'fine-print', 'The extension did not return a frame report.'));
  if (action === 'inspect' || action === 'details') container.append(element('p', 'fine-print', 'Results cover visible fields on this page. Later steps may ask additional questions.'));
}

async function runAction(action) {
  await perform(async () => {
    if (state.dirtyProfile && action === 'details') {
      showNotice('Save your profile changes before filling details.', 'info');
      return;
    }
    const resume = action === 'resume' ? await getResume(state.selectedResumeId) : null;
    if (action === 'resume' && (!resume?.dataUrl || resume.metadata?.missingFile)) {
      throw new Error('Choose an available résumé PDF in Profile first.');
    }
    const report = await runCurrentAction(action, state.profile, resume, { expectedUrl: state.job.url });
    state.report = { ...report, checkedAt: new Date().toISOString(), uiAction: action };
    renderReport(); await persistUi();
    showNotice(action === 'inspect' ? 'Inspection finished. Review the fields listed below.'
      : action === 'resume' ? 'Attachment action finished. Review its result and the application page.'
        : 'Autofill finished. Review the exact results below.', 'info');
    $('#report-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
}

function renderApplications() {
  $('#application-count').textContent = String(state.applications.length);
  const stats = $('#application-stats');
  stats.replaceChildren();
  const counts = {
    Started: state.applications.filter(item => item.status === 'started').length,
    Submitted: state.applications.filter(item => Boolean(item.submittedAt) || item.status === 'submitted').length,
    'In interview': state.applications.filter(item => item.status === 'interview').length,
  };
  for (const [label, count] of Object.entries(counts)) {
    const stat = element('div', 'stat');
    stat.append(element('strong', '', count), element('span', '', label));
    stats.append(stat);
  }
  const filter = $('#application-filter').value;
  const list = $('#application-list');
  list.replaceChildren();
  const applications = [...state.applications]
    .filter(item => !filter || item.status === filter)
    .sort((a, b) => String(b.updatedAt || b.createdAt || '').localeCompare(String(a.updatedAt || a.createdAt || '')));
  if (!applications.length) {
    const empty = element('div', 'card empty-state', filter ? 'No applications with this status.' : 'Your application records will appear here. Capture a job in Apply to start.');
    if (!filter) {
      const button = element('button', 'text-button', 'Go to Apply');
      button.type = 'button'; button.addEventListener('click', () => switchTab('apply'));
      empty.append(button);
    }
    list.append(empty);
  }
  for (const application of applications) {
    const card = element('article', 'application-card');
    const url = validWebUrl(application.url);
    const title = element(url ? 'a' : 'div', 'application-title', application.title || 'Untitled role');
    if (url) { title.href = url; title.target = '_blank'; title.rel = 'noopener noreferrer'; }
    card.append(title, element('p', 'application-company', application.company || 'Company not recorded'));
    const label = element('label', '', 'Status');
    const select = element('select');
    select.setAttribute('aria-label', `Status for ${application.title || 'application'} at ${application.company || 'company'}`);
    for (const [value, text] of Object.entries(STATUS_LABELS)) {
      const option = element('option', '', text); option.value = value; select.append(option);
    }
    select.value = application.status;
    select.addEventListener('change', () => {
      const previous = application.status;
      perform(async () => {
        try {
          await updateApplication(application.id, { status: select.value });
          state.applications = await listApplications(); renderApplications(); await checkDuplicate();
          showNotice('Application status updated.');
        } catch (error) { select.value = previous; throw error; }
      });
    });
    label.append(select); card.append(label);
    const metadata = element('div', 'application-meta');
    const recorded = readableDate(application.submittedAt || application.createdAt);
    if (recorded) metadata.append(element('span', '', `${application.submittedAt ? 'Submitted' : 'Saved'} ${recorded}`));
    if (application.resume_name) metadata.append(element('span', '', `Selected résumé: ${application.resume_name}`));
    if (application.provider) metadata.append(element('span', '', `Portal: ${application.provider}`));
    card.append(metadata);
    if (application.notes) card.append(element('p', 'application-note', application.notes));
    list.append(card);
  }
}

async function recordApplication(status) {
  await perform(async () => {
    state.job = readJob();
    if (!state.job.title || !state.job.company || !validWebUrl(state.job.url)) {
      throw new Error('Add a role, company, and valid job URL before saving an application.');
    }
    const existing = await findApplication(state.job.url);
    if (status === 'submitted' && existing && ['interview', 'offer', 'rejected', 'withdrawn'].includes(existing.status)) {
      showNotice('This application already has an outcome. Change it in Applications if needed.', 'info');
      return;
    }
    const resume = state.resumes.find(item => item.id === state.selectedResumeId);
    await saveApplication({
      ...state.job, notes: state.job.notes || existing?.notes || '',
      resume_id: resume?.id || existing?.resume_id || '',
      resume_name: resume?.name || existing?.resume_name || '', source: 'extension',
    }, status);
    state.applications = await listApplications();
    renderApplications(); await persistUi(); await checkDuplicate();
    showNotice(status === 'submitted' ? 'Recorded as submitted based on your confirmation.'
      : existing && existing.status !== 'started' ? `Record updated. ${STATUS_LABELS[existing.status] || existing.status} status preserved.`
        : 'Application saved as started.');
  });
}

async function downloadBackup() {
  const data = await exportData();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = element('a');
  link.href = url; link.download = `jobpilot-backup-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showNotice('Backup exported. Résumé PDF files are not included.');
}

function cancelImport() {
  state.pendingImport = null;
  $('#import-preview').hidden = true;
  $('#import-replace-profile').checked = false;
  $('#import-data').value = '';
}

async function previewImport(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) throw new Error('This backup is too large. Choose a JobPilot JSON backup under 10 MB.');
  let data;
  try { data = JSON.parse(await file.text()); }
  catch { throw new Error('This file is not valid JSON. Choose a JobPilot backup.'); }
  if (!data || typeof data !== 'object' || Array.isArray(data) || data.schemaVersion !== 1
      || !data.profile || typeof data.profile !== 'object' || Array.isArray(data.profile)
      || !Array.isArray(data.applications) || !Array.isArray(data.resumes)) {
    throw new Error('This does not look like a supported JobPilot backup. Nothing was imported.');
  }
  state.pendingImport = data;
  const name = [data.profile.first_name, data.profile.last_name].filter(value => typeof value === 'string').join(' ') || 'unnamed profile';
  $('#import-summary').textContent = `${name} · ${data.applications.length} application records · ${data.resumes.length} résumé names. Review before importing.`;
  $('#import-replace-profile').checked = false;
  $('#import-preview').hidden = false;
  $('#import-preview').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  showNotice('Backup ready for review. Nothing has been imported yet.', 'info');
}

async function confirmImport() {
  if (!state.pendingImport) return;
  if (state.dirtyProfile) {
    showNotice('Save your current profile changes before importing a backup.', 'info');
    return;
  }
  const result = await importData(state.pendingImport, { replaceProfile: $('#import-replace-profile').checked });
  [state.profile, state.resumes, state.applications] = await Promise.all([getProfile(), listResumes(), listApplications()]);
  renderProfile(); renderResumes(); renderApplications(); cancelImport(); await persistUi();
  const summary = `${result?.applicationsMerged ?? 0} application records merged · ${result?.resumesImported ?? 0} résumé names imported.`;
  showNotice(`${summary} ${result?.profileImported ? 'Imported profile saved.' : 'Your saved profile was preserved.'}`);
}

function bindEvents() {
  for (const button of $$('[data-tab]')) {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = ['apply', 'profile', 'applications'];
      const index = tabs.indexOf(state.tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2
        : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
      switchTab(tabs[next], true);
    });
  }
  $('#job-form').addEventListener('submit', event => event.preventDefault());
  $('#job-form').addEventListener('input', event => {
    const oldUrl = state.job.url;
    state.job = readJob();
    if (oldUrl !== state.job.url) { state.job.supported = null; state.job.capturedAt = null; state.job.provider = ''; }
    renderPortalStatus(); queuePersistUi(); checkDuplicate();
  });
  $('#job-notes').addEventListener('input', () => { state.job = readJob(); queuePersistUi(); });
  $('#capture-job').addEventListener('click', () => perform(async () => {
    const job = await captureCurrentJob();
    const sameUrl = sameApplicationUrl(state.job.url, job.url);
    if (!sameUrl) { state.report = null; renderReport(); }
    state.job = {
      title: job.title || '', company: job.company || '', url: job.url || '',
      notes: sameUrl ? $('#job-notes').value : '', provider: job.provider || '',
      supported: Boolean(job.supported), capturedAt: new Date().toISOString(),
      request_id: job.request_id || '', description: job.description || '',
    };
    renderJob(); await persistUi();
    showNotice('Job captured. Review the role and company before saving.', 'info');
  }));
  $('#inspect-form').addEventListener('click', () => runAction('inspect'));
  $('#fill-details').addEventListener('click', () => runAction('details'));
  $('#attach-resume').addEventListener('click', () => runAction('resume'));
  $('#clear-report').addEventListener('click', () => { state.report = null; renderReport(); queuePersistUi(); });
  $('#save-started').addEventListener('click', () => recordApplication('started'));
  $('#save-submitted').addEventListener('click', () => recordApplication('submitted'));
  $('#apply-resume').addEventListener('change', () => {
    state.selectedResumeId = $('#apply-resume').value;
    updateActionButtons(); queuePersistUi();
  });
  $('#profile-form').addEventListener('input', profileDirty);
  $('#profile-form').addEventListener('submit', event => {
    event.preventDefault();
    perform(async () => {
      state.profile = await saveProfile(readProfile()) || await getProfile();
      renderProfileSummary(); state.dirtyProfile = false;
      $('#profile-save-state').textContent = 'Profile saved.';
      showNotice('Profile saved in this browser.');
    });
  });
  $('#add-work').addEventListener('click', () => {
    const list = $('#work-history');
    $('.empty-state', list)?.remove();
    const entry = workEntry({}, list.children.length); list.append(entry); profileDirty();
    $('[data-field="company"]', entry).focus();
  });
  $('#add-education').addEventListener('click', () => {
    const list = $('#education-history');
    $('.empty-state', list)?.remove();
    const entry = educationEntry({}, list.children.length); list.append(entry); profileDirty();
    $('[data-field="school"]', entry).focus();
  });
  $('#resume-upload').addEventListener('change', () => {
    const file = $('#resume-upload').files[0];
    if (!file) return;
    perform(async () => {
      const metadata = await addResume(file, $('#resume-label').value.trim());
      state.resumes = await listResumes();
      state.selectedResumeId = metadata?.id || state.selectedResumeId;
      renderResumes(); await persistUi();
      $('#resume-upload').value = ''; $('#resume-label').value = '';
      showNotice('Résumé saved locally. It is ready to choose in Apply.');
    });
  });
  $('#application-filter').addEventListener('change', renderApplications);
  $('#refresh-applications').addEventListener('click', () => perform(async () => {
    state.applications = await listApplications(); renderApplications();
  }));
  $('#export-data').addEventListener('click', () => perform(downloadBackup));
  $('#import-data').addEventListener('change', () => {
    const file = $('#import-data').files[0];
    perform(async () => { cancelImport(); await previewImport(file); });
  });
  $('#cancel-import').addEventListener('click', cancelImport);
  $('#confirm-import').addEventListener('click', () => perform(confirmImport));
}

async function init() {
  bindEvents(); setBusy(true);
  try {
    const [profile, resumes, applications, stored] = await Promise.all([
      getProfile(), listResumes(), listApplications(), chrome.storage.local.get(UI_KEY),
    ]);
    state.profile = profile || {};
    state.resumes = Array.isArray(resumes) ? resumes : [];
    state.applications = Array.isArray(applications) ? applications : [];
    const ui = stored?.[UI_KEY];
    if (ui && typeof ui === 'object') {
      if (ui.job && typeof ui.job === 'object') state.job = { ...state.job, ...ui.job };
      state.report = ui.report && typeof ui.report === 'object' ? ui.report : null;
      state.selectedResumeId = typeof ui.selectedResumeId === 'string' ? ui.selectedResumeId : '';
      if (['apply', 'profile', 'applications'].includes(ui.tab)) state.tab = ui.tab;
    }
    if (!state.selectedResumeId) state.selectedResumeId = state.profile.selected_resume_id || '';
    renderProfile(); renderResumes(); renderJob(); renderApplications(); renderReport(); switchTab(state.tab);
  } catch (error) {
    showNotice(`Could not load your local workspace: ${friendlyError(error)}`, 'error');
  } finally { setBusy(false); }
}

init();
