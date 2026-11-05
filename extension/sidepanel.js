import {
  getProfile, saveProfile, listResumes, addResume, getResume, deleteResume,
  listApplications, saveApplication, updateApplication, findApplication,
  exportData, importData, normalizeJobUrl,
  listRuns, saveRun, updateRun,
} from './model.js';
import { captureCurrentJob, runCurrentAction, isSupportedPage, providerFor } from './actions.js';
import { REASONS } from './diagnostics.js';
import { setupPrivacyPanel } from './privacy-panel.js';

const UI_KEY = 'jobpilot_ui_v1';
const STATUS_LABELS = {
  started: 'Started', submitted: 'Submitted', interview: 'Interview',
  rejected: 'Rejected', offer: 'Offer', withdrawn: 'Withdrawn',
};
const PROFILE_FIELDS = [
  'first_name', 'last_name', 'preferred_name', 'email', 'phone', 'address_line1', 'address_line2', 'city', 'state', 'postal_code',
  'country', 'linkedin_url', 'github_url', 'portfolio_url', 'current_title',
];
const DISCLOSURE_FIELDS = ['veteran_status', 'other_demographics'];
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const state = {
  profile: {}, resumes: [], applications: [], runs: [], selectedResumeId: '',
  job: { title: '', company: '', url: '', notes: '', provider: '', supported: null },
  activeTab: null, report: null, tab: 'apply', busy: false, pendingImport: null, dirtyProfile: false, jobDrawerTouched: false,
};
let draftTimer;
let duplicateGeneration = 0;
let busyControls = new Map();

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
  if (!['apply', 'profile', 'applications', 'history'].includes(tab)) return;
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
  $('main').inert = busy;
  if (busy) {
    busyControls = new Map($$('main button, main input, main select, main textarea')
      .filter(control => !control.closest('[aria-label="Local assistant connection"]'))
      .map(control => [control, control.disabled]));
    for (const control of busyControls.keys()) control.disabled = true;
  } else {
    for (const [control, disabled] of busyControls) if (control.isConnected) control.disabled = disabled;
    // These lists can be rebuilt while the operation is pending. Their newly
    // rendered controls inherit busy=true and are not in the original snapshot.
    for (const control of $$('#application-list select, #resume-library button, #run-history button')) {
      if (!busyControls.has(control)) control.disabled = false;
    }
    busyControls.clear();
  }
  $('#open-workspace').setAttribute('aria-disabled', String(busy));
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
  if ($('#prepare-application')) $('#prepare-application').disabled = state.busy;
  $('#attach-resume').disabled = state.busy || !resume || Boolean(resume.missingFile);
  $('#report-problem').disabled = state.busy || !state.runs.some(run => run.id === state.report?.diagnosticId);
}

function deriveJobFallbacks(url, rawTitle = '') {
  let title = String(rawTitle || '').trim();
  let company = '';
  const atMatch = /^(.*?)\s+(?:at|@|-|–|—|\|)\s+([^|–—-]+)$/i.exec(title);
  if (atMatch) {
    title = atMatch[1].trim() || title;
    company = atMatch[2].trim();
  }
  if (!company && validWebUrl(url)) {
    try {
      const parsed = new URL(url);
      const parts = parsed.pathname.split('/').filter(Boolean);
      const provider = providerFor(url);
      if (['Greenhouse', 'Lever', 'Ashby'].includes(provider) && parts[0] && !['jobs', 'embed', 'job'].includes(parts[0].toLowerCase())) {
        company = parts[0].replace(/[-_]+/g, ' ');
      } else if (provider === 'Workday') {
        const sub = parsed.hostname.split('.')[0];
        company = (!/^wd\d+$/i.test(sub) ? sub : parts[1] || sub).replace(/[-_]+/g, ' ');
      } else {
        company = parsed.hostname.replace(/^www\./i, '');
      }
    } catch { /* ignore */ }
  }
  return { title: title || 'Application', company: company || 'Employer' };
}

function readJob() {
  return {
    ...state.job,
    title: $('#job-title').value.trim(), company: $('#job-company').value.trim(),
    url: $('#job-url').value.trim(), notes: $('#job-notes').value.trim(),
  };
}

function updateJobDrawerPill() {
  const drawer = $('#job-details-drawer');
  const pill = $('#job-summary-action');
  if (drawer && pill) pill.textContent = drawer.open ? 'Hide fields' : 'Edit / notes';
}

function renderJob() {
  for (const key of ['title', 'company', 'url', 'notes']) $(`#job-${key}`).value = state.job[key] || '';
  const summaryParts = [state.job.title, state.job.company].filter(Boolean);
  if (state.job.notes) summaryParts.push('Note added');
  if ($('#job-summary-text')) {
    $('#job-summary-text').textContent = summaryParts.length ? summaryParts.join(' · ') : 'Enter or capture role & company';
  }
  const drawer = $('#job-details-drawer');
  if (drawer) {
    const hasCapturedOrNamedJob = Boolean(state.job.url && (state.job.title || state.job.company));
    if (!hasCapturedOrNamedJob) {
      drawer.open = true;
    } else if (!state.jobDrawerTouched) {
      drawer.open = false;
    }
    updateJobDrawerPill();
  }
  renderPortalStatus();
  checkDuplicate();
}

function updateSecurityCodeVisibility() {
  const section = $('#security-code-section');
  if (!section) return;
  const activeUrl = state.activeTab?.url || '';
  const matchesActive = !activeUrl || sameApplicationUrl(state.job.url, activeUrl);
  const provider = matchesActive && state.job.provider
    ? state.job.provider
    : providerFor(activeUrl || state.job.url);
  const hasTypedCode = Boolean($('#security-code-input')?.value.trim());
  const frames = safeReportFrames(state.report);
  const reportUrl = validWebUrl(state.report?.pageUrl || frames[0]?.pageUrl);
  const currentReport = !reportUrl || !activeUrl || sameApplicationUrl(reportUrl, activeUrl);
  const reportHasSecurityCode = currentReport && frames.some(frame => {
    if (frame?.securityCode?.status && frame.securityCode.status !== 'not-requested') return true;
    const rows = [...(frame.filled || []), ...(frame.skipped || []), ...(frame.required || []), ...(frame.failed || [])];
    return rows.some(row => row?.type === 'security-code' || row?.key === 'security_code'
      || /\b(security|verification)\s*code\b/i.test(row?.label || '')
      || /Fill code/i.test(row?.reason || ''));
  });
  section.hidden = !(provider === 'Greenhouse' || hasTypedCode || reportHasSecurityCode);
}

async function refreshActiveTab() {
  try {
    const [tab] = await (chrome.tabs?.query?.({ active: true, currentWindow: true }) || []);
    state.activeTab = tab ? { id: tab.id, url: tab.url || '', title: tab.title || '' } : null;
  } catch {
    state.activeTab = null;
  }
  renderPortalStatus();
  renderReport();
}

function renderPortalStatus() {
  const status = $('#portal-status');
  status.replaceChildren();
  const dot = element('span', 'status-dot');
  dot.setAttribute('aria-hidden', 'true');
  const captured = Boolean(state.job.capturedAt);
  const activeUrl = state.activeTab?.url || '';
  const matchesActive = !activeUrl || sameApplicationUrl(state.job.url, activeUrl);
  let message = 'Click JobPilot toolbar icon on a job tab to check availability.';
  if (captured && matchesActive && state.job.supported === true) {
    dot.classList.add('supported');
    message = state.job.provider === 'Workday'
      ? 'Workday · Ready to inspect, fill details, or attach PDF on this step.'
      : `${state.job.provider || 'Supported portal'} · Autofill ready (fill details, attach PDF, inspect)`;
  } else if (captured && matchesActive && state.job.supported === false) {
    dot.classList.add('unsupported');
    message = 'Manual tracking only · Autofill supports Greenhouse, Lever, Ashby, and Workday.';
  } else if (activeUrl) {
    if (isSupportedPage(activeUrl)) {
      const provider = providerFor(activeUrl);
      dot.classList.add('supported');
      message = provider === 'Workday'
        ? 'Workday · Ready to inspect, fill details, or attach PDF on this step.'
        : `${provider} · Autofill ready (fill details, attach PDF, inspect)`;
    } else if (validWebUrl(activeUrl)) {
      dot.classList.add('unsupported');
      message = 'Manual tracking only · Capture tab to save this job.';
    } else {
      message = 'Open a job application webpage to begin.';
    }
  } else if (state.job.url) {
    if (isSupportedPage(state.job.url)) {
      const provider = providerFor(state.job.url);
      dot.classList.add('supported');
      message = provider === 'Workday'
        ? 'Workday · Click JobPilot on the tab, then inspect or fill this step.'
        : `${provider} · Autofill ready on this page`;
    } else {
      message = 'Job draft saved · Click Capture tab on the job page.';
    }
  }
  status.append(dot, element('span', '', message));
  updateSecurityCodeVisibility();
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
      if (state.activeTab?.id && sameApplicationUrl(state.job.url, state.activeTab.url)) {
        const closeBtn = element('button', 'text-button', 'Close tab');
        closeBtn.type = 'button';
        closeBtn.addEventListener('click', () => perform(async () => {
          await closeTabById(state.activeTab.id);
          showNotice('Closed the already-saved job tab.', 'info');
        }));
        notice.append(document.createTextNode(' · '), closeBtn);
      }
    }
  } catch (error) {
    if (generation === duplicateGeneration) { notice.hidden = true; }
  }
}

function renderProfileSummary() {
  const container = $('#profile-summary');
  const name = [state.profile.first_name, state.profile.last_name].filter(Boolean).join(' ');
  const hasProfile = Boolean(name || state.profile.email || state.profile.work_history?.length || DISCLOSURE_FIELDS.some(key => state.profile.disclosure_preferences?.[key]));
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
    if (kind === 'work') {
      $('[data-field="start_date"]', entry).setAttribute('aria-label', `Position ${index + 1} start date, year and month`);
      $('[data-field="end_date"]', entry).setAttribute('aria-label', `Position ${index + 1} end date, year and month`);
    }
  });
}

function renderProfile() {
  const form = $('#profile-form');
  for (const key of PROFILE_FIELDS) form.elements.namedItem(key).value = state.profile[key] || '';
  for (const key of ['work_authorized', 'requires_sponsorship']) {
    form.elements.namedItem(key).value = state.profile[key] === true ? 'yes' : state.profile[key] === false ? 'no' : '';
  }
  for (const key of DISCLOSURE_FIELDS) form.elements.namedItem(`disclosure_${key}`).value = state.profile.disclosure_preferences?.[key] || '';
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
  result.disclosure_preferences = Object.fromEntries(DISCLOSURE_FIELDS.map(key => [key, $('#profile-form').elements.namedItem(`disclosure_${key}`).value]));
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

function renderChecklistRow(container, badgeText, text, warn = false) {
  const row = element('div', 'checklist-row');
  row.append(element('span', `checklist-badge${warn ? ' warn' : ''}`, badgeText), element('span', '', text));
  container.append(row);
}

function renderReport() {
  const card = $('#report-card');
  card.hidden = !state.report;
  updateSecurityCodeVisibility();
  if (!state.report) return;
  const report = state.report;
  const frames = safeReportFrames(report);
  const reportUrl=validWebUrl(report.pageUrl || frames[0]?.pageUrl);
  const activeUrl=validWebUrl(state.activeTab?.url);
  const differentApplication=!!reportUrl && !!activeUrl && !sameApplicationUrl(reportUrl,activeUrl);
  const provenance=$('#report-current-warning');
  provenance.hidden=!differentApplication;
  provenance.replaceChildren();
  if(differentApplication) {
    const original=element('a','','another application');original.href=reportUrl;original.target='_blank';original.rel='noopener';
    provenance.append(document.createTextNode('These saved results belong to '),original,
      document.createTextNode('. Click Inspect fields to check the current tab.'));
  }
  const groups = { filled: [], skipped: [], failed: [], required: [] };
  const warnings = Array.isArray(report.warnings) ? [...report.warnings] : [];
  for (const frame of frames) {
    for (const key of Object.keys(groups)) {
      if (Array.isArray(frame[key])) groups[key].push(...frame[key].map(row => ({ ...row, frameId: frame.frameId })));
    }
    if (frame.error) groups.failed.push({ label: `Frame ${frame.frameId ?? 'unknown'}`, reason: String(frame.error) });
    if (Array.isArray(frame.warnings)) warnings.push(...frame.warnings);
  }
  if (report.error) groups.failed.push({label: 'Action interrupted', reason: String(report.error)});
  const interrupted = report.status === 'interrupted';
  const incomplete = interrupted || report.status === 'partial' || groups.failed.length > 0
    || frames.some(frame => ['partial', 'interrupted'].includes(frame.status));
  const action = report.uiAction || frames[0]?.action || 'inspect';
  $('#report-heading').textContent = differentApplication ? 'Saved results from another application' : action === 'resume' ? 'Résumé result'
    : action === 'details' ? 'Autofill results'
    : action === 'security-code' ? 'Security code result'
    : 'Field inspection';
  $('#report-context').textContent = [hostOf(report.pageUrl || frames[0]?.pageUrl), readableTime(report.checkedAt)].filter(Boolean).join(' · ');
  let summary;
  if (report.status === 'running') {
    summary = 'Action in progress. Results appear as each form finishes.';
  } else if (action === 'inspect') {
    summary = groups.required.length
      ? `${groups.required.length} visible required ${groups.required.length === 1 ? 'field needs' : 'fields need'} attention.`
      : incomplete ? 'Inspection was incomplete. The remaining required fields could not be verified.'
        : frames.length ? 'No empty visible required fields were detected.' : 'No form results were returned.';
  } else if (action === 'details') {
    summary = `${incomplete ? interrupted ? 'Autofill interrupted. ' : 'Autofill needs attention. ' : ''}${groups.filled.length} ${groups.filled.length === 1 ? 'field' : 'fields'} confirmed filled · ${groups.skipped.length} skipped · ${groups.failed.length} failed.`;
  } else if (action === 'security-code') {
    summary = groups.filled.length
      ? '1 security code challenge filled on the page.'
      : incomplete ? 'Security code action needs attention. Review the message below.'
        : 'No security code field was filled on this page.';
  } else {
    summary = 'Review the résumé result below and the attachment shown on the application page.';
  }
  $('#report-summary').textContent = summary;
  const allResumeResults = frames.map(frame => frame.resume).filter(item => item && typeof item === 'object');
  const activeResumeResults = allResumeResults.filter(item => item.status && item.status !== 'not-requested');
  const checklist = $('#report-checklist');
  if (checklist) {
    checklist.replaceChildren();
    checklist.hidden = false;
    if (report.status === 'running') {
      renderChecklistRow(checklist, '⏳', 'Done for you: Preparing this application form…');
      renderChecklistRow(checklist, '→', 'Left for you: Wait for form results before reviewing the page.');
    } else {
      const attached = activeResumeResults.find(item => ['accepted', 'prepared', 'already-attached'].includes(item.status));
      const failedResume = activeResumeResults.find(item => item.status === 'failed');
      const doneParts = [];
      if (action === 'details') {
        doneParts.push(`Filled ${groups.filled.length} ${groups.filled.length === 1 ? 'field' : 'fields'}`);
      } else if (action === 'inspect') {
        doneParts.push(`Inspected form (${groups.filled.length} filled)`);
      } else if (action === 'security-code') {
        doneParts.push(groups.filled.length ? 'Filled security code' : 'Checked security code field');
      }
      if (attached) {
        doneParts.push(`Attached ${attached.filename || 'selected PDF'}`);
      } else if (failedResume) {
        doneParts.push('PDF attachment needs review');
      } else if (action === 'resume') {
        doneParts.push('Checked résumé upload');
      }
      renderChecklistRow(
        checklist,
        interrupted ? '!' : '✓',
        `Done for you: ${doneParts.join(' · ') || 'Checked form'}`,
        interrupted,
      );
      if (interrupted) {
        renderChecklistRow(checklist, '→', 'Left for you: Inspect the current page before retrying.', true);
      } else {
        const remainingLabels = [...new Set(
          [...groups.failed, ...groups.required]
            .map(row => row?.label)
            .filter(label => typeof label === 'string' && label.trim() && !/^Frame\s+/i.test(label) && label !== 'Action interrupted'),
        )];
        if (remainingLabels.length) {
          const preview = remainingLabels.slice(0, 3).join(', ') + (remainingLabels.length > 3 ? '…' : '');
          renderChecklistRow(
            checklist,
            '→',
            `Left for you on page (${remainingLabels.length}): ${preview}`,
            groups.failed.length > 0,
          );
        } else {
          renderChecklistRow(checklist, '→', 'Left for you on page: No required gaps detected — review the page and submit when ready.');
        }
      }
    }
  }
  const upload = $('#report-upload');
  upload.replaceChildren(); upload.hidden = true; upload.className = 'inline-note';
  if (action === 'resume' || activeResumeResults.length) {
    const resumeResults = action === 'resume' ? allResumeResults : activeResumeResults;
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
  } else if (action === 'security-code') {
    const codeResults = frames.map(frame => frame.securityCode).filter(item => item && typeof item === 'object');
    if (codeResults.length) {
      upload.hidden = false;
      upload.textContent = codeResults.map(item => item.message || '').filter(Boolean).join('\n');
      if (codeResults.every(item => item.status === 'filled')) upload.classList.add('good');
    }
  }
  const container = $('#report-details');
  container.replaceChildren();
  if (incomplete) container.append(element('p', 'inline-note', 'Some fields may already be filled. Inspect the current page, then retry the same action to continue. Existing answers are preserved.'));
  reportGroup(container, 'Needs review', [...new Set(warnings.filter(item => typeof item === 'string' && item.trim()))], row => row, true);
  reportGroup(container, 'Filled', groups.filled, row => row.label || row.key || 'Unlabeled field');
  reportGroup(container, 'Still required', groups.required, row => `${row.label || 'Unlabeled field'}${row.type ? ` · ${row.type}` : ''}`, true);
  reportGroup(container, 'Skipped', groups.skipped, row => `${row.label || 'Unlabeled field'} · ${row.reason || 'Not filled'}`);
  reportGroup(container, 'Failed', groups.failed, row => `${row.label || 'Unlabeled field'} · ${row.reason || 'Could not fill'}`, true);
  if (!frames.length) container.append(element('p', 'fine-print', 'The extension did not return a frame report.'));
  const saved = state.runs.find(run => run.id === report.diagnosticId);
  $('#report-log-status').textContent = saved ? `Saved in History · Run ${saved.id.slice(0, 8)}${saved.reported ? ' · Problem reported' : ''}`
    : 'This report has not been saved in History.';
}

async function checkpointRun(action, job, report = state.report) {
  try {
    const saved = await saveRun({id: report.diagnosticId, action, job,
      startedAt: report.startedAt || report.checkedAt, legacy: report.legacy === true, report});
    state.runs = [saved, ...state.runs.filter(run => run.id !== saved.id)].slice(0, 50);
    renderHistory(); renderReport();
    return true;
  } catch {
    // Logging must never replay a completed form mutation or hide its outcome.
    $('#report-log-status').textContent = 'Could not save this run in History. The current report remains available.';
    return false;
  }
}

function mergePrepareReport(detailsReport, resumeReport) {
  const detailsFrames = safeReportFrames(detailsReport);
  const resumeFrames = safeReportFrames(resumeReport);
  const resumeByFrame = new Map(resumeFrames.map(frame => [frame.frameId ?? 0, frame]));
  const mergedFrames = detailsFrames.map(frame => {
    const rFrame = resumeByFrame.get(frame.frameId ?? 0);
    if (!rFrame) return frame;
    const resumeObj = rFrame.resume || frame.resume;
    const resumeHandled = ['accepted', 'prepared', 'already-attached'].includes(resumeObj?.status);
    const skipped = (frame.skipped || []).filter(row => !(resumeHandled && row?.reason === 'Use Attach résumé separately'));
    const required = (!rFrame.error && rFrame.status !== 'interrupted' && Array.isArray(rFrame.required))
      ? rFrame.required
      : (frame.required || []).filter(row => !(resumeHandled && row?.type === 'file'));
    const failed = [...(frame.failed || []), ...(rFrame.failed || [])];
    const warnings = [...new Set([...(frame.warnings || []), ...(rFrame.warnings || [])])];
    const error = frame.error || rFrame.error;
    const status = (frame.status === 'interrupted' || rFrame.status === 'interrupted') ? 'interrupted'
      : (frame.status === 'partial' || rFrame.status === 'partial' || failed.length > 0) ? 'partial'
        : 'complete';
    return {
      ...frame,
      status,
      ...(error ? {error} : {}),
      skipped,
      required,
      failed,
      warnings,
      ...(resumeObj ? {resume: resumeObj} : {}),
    };
  });
  const warnings = [...new Set([...(detailsReport.warnings || []), ...(resumeReport.warnings || [])])];
  const error = detailsReport.error || resumeReport.error;
  const incomplete = mergedFrames.some(frame => frame.error || ['partial', 'interrupted'].includes(frame.status) || frame.failed?.length);
  const completed = mergedFrames.some(frame => !frame.error && frame.status !== 'interrupted');
  const status = resumeReport.status === 'running' ? 'running'
    : (detailsReport.status === 'interrupted' || resumeReport.status === 'interrupted') ? (completed ? 'partial' : 'interrupted')
      : incomplete ? 'partial' : 'complete';
  return {
    ...detailsReport,
    status,
    ...(error ? {error} : {}),
    warnings,
    frames: mergedFrames,
    checkedAt: resumeReport.checkedAt || detailsReport.checkedAt,
    startedAt: detailsReport.startedAt || detailsReport.checkedAt,
    uiAction: 'details',
    runAction: 'resume',
    diagnosticId: resumeReport.diagnosticId || detailsReport.diagnosticId,
  };
}

async function prepareApplication() {
  await perform(async () => {
    if (state.dirtyProfile) {
      showNotice('Save your profile changes before preparing an application.', 'info');
      return;
    }
    try {
      const [activeTab] = await (chrome.tabs?.query?.({ active: true, currentWindow: true }) || []);
      if (activeTab?.url) {
        state.activeTab = { id: activeTab.id, url: activeTab.url, title: activeTab.title || '' };
        if (isSupportedPage(activeTab.url)) {
          const sameUrl = sameApplicationUrl(state.job.url, activeTab.url);
          let captured = null;
          try { captured = await captureCurrentJob(); } catch { /* fall back to tab metadata */ }
          const targetUrl = captured?.url || activeTab.url;
          const fallback = deriveJobFallbacks(targetUrl, captured?.title || activeTab.title || '');
          state.jobDrawerTouched = false;
          state.job = {
            title: captured?.title || (sameUrl && state.job.title) || fallback.title,
            company: captured?.company || (sameUrl && state.job.company) || fallback.company,
            url: targetUrl,
            notes: sameUrl ? ($('#job-notes').value.trim() || state.job.notes || '') : '',
            provider: captured?.provider || providerFor(targetUrl),
            supported: captured ? Boolean(captured.supported) : true,
            capturedAt: new Date().toISOString(),
            request_id: captured?.request_id || '',
            description: captured?.description || '',
          };
          renderJob();
        }
      }
    } catch { /* runCurrentAction will surface any tab error */ }

    const checkedAt = new Date().toISOString();
    const detailsDiagnosticId = crypto.randomUUID();
    const job = {...state.job};
    state.report = {pageUrl: job.url, frames: [], status: 'running', checkedAt, startedAt: checkedAt, uiAction: 'details', runAction: 'details', diagnosticId: detailsDiagnosticId};
    renderReport();

    let detailsReport;
    try {
      await checkpointRun('details', job, state.report);
      await persistUi();
      detailsReport = await runCurrentAction('details', state.profile, null, {
        expectedUrl: job.url,
        onProgress: async progress => {
          state.report = {...progress, checkedAt, startedAt: checkedAt, uiAction: 'details', runAction: 'details', diagnosticId: detailsDiagnosticId};
          renderReport();
          await checkpointRun('details', job, state.report);
          await persistUi();
        },
      });
    } catch (error) {
      state.report = {...state.report, status: 'interrupted', error: friendlyError(error)};
      await checkpointRun('details', job, state.report);
      renderReport(); await persistUi();
      showNotice(friendlyError(error), 'error');
      return;
    }

    detailsReport = {...detailsReport, checkedAt: new Date().toISOString(), startedAt: checkedAt, uiAction: 'details', runAction: 'details', diagnosticId: detailsDiagnosticId};
    state.report = detailsReport;
    let logged = await checkpointRun('details', job, detailsReport);
    renderReport(); await persistUi();

    const resume = state.selectedResumeId ? await getResume(state.selectedResumeId) : null;
    const hasValidResume = Boolean(resume?.dataUrl && !resume.metadata?.missingFile);
    const workdayFrames = safeReportFrames(detailsReport).filter(frame => frame?.workdayScope);
    const canAttachOnStep = !workdayFrames.length || workdayFrames.some(frame => frame.workdayScope?.canAttachResume);

    if (detailsReport.status !== 'interrupted' && hasValidResume && canAttachOnStep) {
      const resumeStartedAt = new Date().toISOString();
      const resumeDiagnosticId = crypto.randomUUID();
      let resumeRunReport = {pageUrl: job.url, frames: [], status: 'running', checkedAt: resumeStartedAt, startedAt: resumeStartedAt, uiAction: 'resume', diagnosticId: resumeDiagnosticId};
      state.report = mergePrepareReport(detailsReport, resumeRunReport);
      renderReport();
      try {
        await checkpointRun('resume', job, resumeRunReport);
        await persistUi();
        const resumeResult = await runCurrentAction('resume', {}, resume, {
          expectedUrl: job.url,
          onProgress: async progress => {
            resumeRunReport = {...progress, checkedAt: resumeStartedAt, startedAt: resumeStartedAt, uiAction: 'resume', diagnosticId: resumeDiagnosticId};
            state.report = mergePrepareReport(detailsReport, resumeRunReport);
            renderReport();
            await checkpointRun('resume', job, resumeRunReport);
            await persistUi();
          },
        });
        resumeRunReport = {...resumeResult, checkedAt: new Date().toISOString(), startedAt: resumeStartedAt, uiAction: 'resume', diagnosticId: resumeDiagnosticId};
        state.report = mergePrepareReport(detailsReport, resumeRunReport);
        logged = (await checkpointRun('resume', job, resumeRunReport)) && logged;
        renderReport(); await persistUi();
      } catch (error) {
        resumeRunReport = {...resumeRunReport, status: 'interrupted', error: friendlyError(error)};
        state.report = mergePrepareReport(detailsReport, resumeRunReport);
        await checkpointRun('resume', job, resumeRunReport);
        renderReport(); await persistUi();
        showNotice(friendlyError(error), 'error');
        return;
      }
    }

    const incomplete = ['partial', 'interrupted'].includes(state.report.status);
    showNotice(!logged ? 'The action finished, but its history could not be saved. Review the current report.'
      : incomplete ? 'Application partially prepared. Review the checklist below for remaining items.'
        : 'Application prepared. Review the checklist below.', 'info');
    $('#report-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
}

async function runAction(action) {
  let focusSecurityCode = false;
  await perform(async () => {
    if (state.dirtyProfile && action === 'details') {
      showNotice('Save your profile changes before filling details.', 'info');
      return;
    }
    const securityCode = action === 'security-code' ? $('#security-code-input').value : '';
    if (action === 'security-code' && !securityCode.trim()) {
      focusSecurityCode = true;
      throw new Error('Enter the 8-character security code from your Greenhouse email first.');
    }
    try {
      const [activeTab] = await (chrome.tabs?.query?.({ active: true, currentWindow: true }) || []);
      if (activeTab?.url) {
        state.activeTab = { id: activeTab.id, url: activeTab.url, title: activeTab.title || '' };
        if (isSupportedPage(activeTab.url) && (!state.job.url || !sameApplicationUrl(state.job.url, activeTab.url))) {
          const hadUrl = Boolean(state.job.url);
          state.jobDrawerTouched = false;
          state.job = {
            title: (!hadUrl && state.job.title) || activeTab.title || '',
            company: hadUrl ? '' : state.job.company || '',
            url: activeTab.url,
            notes: hadUrl ? '' : state.job.notes || '',
            provider: providerFor(activeTab.url),
            supported: true, capturedAt: new Date().toISOString(),
            request_id: '', description: '',
          };
          renderJob();
        }
      }
    } catch { /* Proceed to runCurrentAction which handles tab errors cleanly. */ }
    const checkedAt = new Date().toISOString();
    const diagnosticId = crypto.randomUUID();
    const job = {...state.job};
    state.report = {pageUrl: job.url, frames: [], status: 'running', checkedAt, startedAt: checkedAt, uiAction: action, diagnosticId};
    renderReport();
    // Save the operation before sending profile data. If the panel closes, its
    // next opening must explain that the result is unknown rather than show an
    // older, successful action as the current result.
    let report;
    try {
      await checkpointRun(action, job);
      await persistUi();
      const resume = action === 'resume' ? await getResume(state.selectedResumeId) : null;
      if (action === 'resume' && (!resume?.dataUrl || resume.metadata?.missingFile)) throw new Error('Choose a résumé with a local file first.');
      report = await runCurrentAction(action, state.profile, resume, {
        expectedUrl: job.url,
        securityCode,
        onProgress: async progress => {
          state.report = {...progress, checkedAt, startedAt: checkedAt, uiAction: action, diagnosticId};
          renderReport();
          await checkpointRun(action, job);
          await persistUi();
        },
      });
    } catch (error) {
      state.report = {...state.report, status: 'interrupted', error: friendlyError(error)};
      await checkpointRun(action, job);
      renderReport(); await persistUi();
      showNotice(friendlyError(error), 'error');
      return;
    }
    state.report = { ...report, checkedAt: new Date().toISOString(), startedAt: checkedAt, uiAction: action, diagnosticId };
    const logged = await checkpointRun(action, job);
    renderReport(); await persistUi();
    const incomplete = ['partial', 'interrupted'].includes(report.status);
    const securityStatus = report.frames?.find(frame => frame?.securityCode)?.securityCode?.status;
    const needsAttention = incomplete || (action === 'security-code' && securityStatus !== 'filled');
    showNotice(!logged ? 'The action finished, but its history could not be saved. Review the current report.'
      : needsAttention ? 'The action needs attention. Review the results, inspect the current page, and retry to continue.'
      : action === 'inspect' ? 'Inspection finished. Review the fields listed below.'
      : action === 'resume' ? 'Attachment action finished. Review its result and the application page.'
      : action === 'security-code' ? 'Security code filled. Review the page and submit your application.'
        : 'Autofill finished. Review the exact results below.', 'info');
    $('#report-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  if (focusSecurityCode) $('#security-code-input')?.focus();
}

const isProblem = run => run.health === 'problem' || run.reported;

function renderHistory() {
  const open = state.runs.filter(run => isProblem(run) && !run.resolved);
  $('#problem-count').textContent = String(open.length);
  const container = $('#run-history');
  container.replaceChildren();
  const runs = $('#history-filter').value === 'all' ? state.runs : open;
  if (!runs.length) {
    container.append(element('p', 'empty-state', state.runs.length ? 'No open problems. Choose All runs to review saved results.' : 'Runs will appear here after you inspect, fill details, or attach a résumé.'));
    return;
  }
  for (const run of runs) {
    const card = element('section', 'card report-card');
    card.append(element('h3', '', `${{inspect: 'Inspect fields', details: 'Fill details', resume: 'Attach résumé', 'security-code': 'Fill security code'}[run.action]} · ${run.company || run.provider}`));
    const status = run.resolved ? 'Resolved' : run.reported ? 'Reported problem' : ({problem: 'Needs investigation', manual: 'Manual answers remain', running: 'Final result not yet known', ok: 'Completed'}[run.health]);
    card.append(element('p', 'fine-print', `${readableTime(run.startedAt)} · ${status} · ${run.version ? `v${run.version}` : 'version unknown'} · Run ${run.id.slice(0, 8)}`));
    if (run.title) card.append(element('p', 'fine-print', run.title));
    if (run.url) {
      const link = element('a', 'fine-print', 'Open job');
      link.href = run.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link);
    }
    const reasons = [...run.warnings, ...(run.error ? [run.error] : []), ...run.frames.flatMap(frame => [...frame.warnings, ...(frame.error ? [frame.error] : [])])];
    reportGroup(card, 'Errors and warnings', [...new Set(reasons)], reason => REASONS[reason] || REASONS.unknown, true);
    for (const [key, heading] of [['failed', 'Failed'], ['required', 'Still required'], ['skipped', 'Skipped'], ['filled', 'Filled']]) {
      reportGroup(card, heading, run.frames.flatMap(frame => frame[key]), row => `${row.label}${row.type ? ` · ${row.type}` : ''}${row.reason ? ` · ${REASONS[row.reason] || REASONS.unknown}` : ''}`, key === 'failed');
    }
    for (const frame of run.frames.filter(frame => frame.resume.status !== 'not-requested')) {
      card.append(element('p', 'fine-print', `Résumé: ${frame.resume.status}${frame.resume.reason ? ` · ${REASONS[frame.resume.reason] || REASONS.unknown}` : ''}`));
    }
    if (run.truncated) card.append(element('p', 'fine-print', 'Some fields were omitted to keep this log within the storage limit.'));
    const actions = element('div', 'action-grid');
    const download = element('button', 'button small', 'Export run'); download.type = 'button';
    download.disabled = state.busy;
    download.addEventListener('click', () => downloadRuns([run])); actions.append(download);
    const review = element('button', 'button small', run.resolved ? 'Reopen' : isProblem(run) ? 'Mark resolved' : 'Report a problem');
    review.type = 'button'; review.disabled = state.busy;
    review.addEventListener('click', () => perform(async () => {
      await updateRun(run.id, run.resolved ? {resolved: false} : isProblem(run) ? {resolved: true} : {reported: true});
      state.runs = await listRuns(); renderHistory(); renderReport();
    }));
    actions.append(review); card.append(actions); container.append(card);
  }
}

function downloadRuns(runs) {
  if (!runs.length) { showNotice('There are no open problems to export.', 'info'); return; }
  const data = {schemaVersion: 1, exportedAt: new Date().toISOString(), runs,
    note: 'Local diagnostics only. No answers, profile values, file names, file bytes, HTML, or raw exceptions.'};
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const link = element('a'); link.href = url;
  link.download = `jobpilot-diagnostics-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showNotice('Diagnostics exported. Nothing was sent to a server.');
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

async function closeTabById(tabId, closedUrl = '') {
  if (!Number.isInteger(tabId)) return false;
  await chrome.tabs?.remove?.(tabId);
  const urlToClear = closedUrl || state.activeTab?.url || '';
  if (!urlToClear || sameApplicationUrl(state.job.url, urlToClear)) {
    state.jobDrawerTouched = false;
    state.job = { title: '', company: '', url: '', notes: '', provider: '', supported: null };
    state.report = null;
  }
  await refreshActiveTab();
  renderJob();
  renderReport();
  await persistUi();
  return true;
}

async function submitAndCloseTab() {
  await perform(async () => {
    state.job = readJob();
    const [activeTab] = await (chrome.tabs?.query?.({ active: true, currentWindow: true }) || []);
    if (activeTab?.url) state.activeTab = { id: activeTab.id, url: activeTab.url, title: activeTab.title || '' };
    const activeUrl = validWebUrl(activeTab?.url);
    const matchesOrEmpty = !state.job.url || (activeUrl && sameApplicationUrl(state.job.url, activeUrl));
    if (matchesOrEmpty && (!state.job.title || !state.job.company || !validWebUrl(state.job.url))) {
      let captured = null;
      if (activeUrl && isSupportedPage(activeUrl)) {
        try { captured = await captureCurrentJob(); } catch { /* ignore capture error */ }
      }
      const targetUrl = validWebUrl(state.job.url) || validWebUrl(captured?.url) || activeUrl || '';
      const fallback = deriveJobFallbacks(targetUrl, captured?.title || activeTab?.title || '');
      state.job = {
        ...state.job,
        title: state.job.title || captured?.title || fallback.title,
        company: state.job.company || captured?.company || fallback.company,
        url: targetUrl,
        provider: state.job.provider || captured?.provider || providerFor(targetUrl),
      };
      renderJob();
    }
    if (!state.job.title || !state.job.company || !validWebUrl(state.job.url)) {
      throw new Error('Add a role, company, and valid job URL before saving an application.');
    }
    const existing = await findApplication(state.job.url);
    if (!existing || !['interview', 'offer', 'rejected', 'withdrawn'].includes(existing.status)) {
      const resume = state.resumes.find(item => item.id === state.selectedResumeId);
      await saveApplication({
        ...state.job, notes: state.job.notes || existing?.notes || '',
        resume_id: resume?.id || existing?.resume_id || '',
        resume_name: resume?.name || existing?.resume_name || '', source: 'extension',
      }, 'submitted');
      state.applications = await listApplications();
      renderApplications();
    }
    const canCloseActive = Number.isInteger(activeTab?.id) && activeUrl && sameApplicationUrl(state.job.url, activeUrl);
    if (canCloseActive) {
      await closeTabById(activeTab.id, activeUrl);
      showNotice('Recorded as submitted and closed the job tab.');
    } else {
      await persistUi(); await checkDuplicate();
      showNotice('Recorded as submitted based on your confirmation.');
    }
  });
}

async function passAndCloseTab() {
  await perform(async () => {
    const [activeTab] = await (chrome.tabs?.query?.({ active: true, currentWindow: true }) || []);
    if (activeTab?.url) state.activeTab = { id: activeTab.id, url: activeTab.url, title: activeTab.title || '' };
    const activeUrl = validWebUrl(activeTab?.url);
    const targetUrl = activeUrl || validWebUrl(state.job.url);
    if (targetUrl) {
      const existing = await findApplication(targetUrl);
      if (existing?.status === 'started') {
        await updateApplication(existing.id, { status: 'withdrawn' });
        state.applications = await listApplications();
        renderApplications();
      }
    }
    if (Number.isInteger(activeTab?.id) && activeUrl) {
      await closeTabById(activeTab.id, activeUrl);
      showNotice('Passed on this role and closed the job tab.', 'info');
    } else {
      state.jobDrawerTouched = false;
      state.job = { title: '', company: '', url: '', notes: '', provider: '', supported: null };
      state.report = null;
      renderJob(); renderReport(); await persistUi();
      showNotice('Cleared current job draft.', 'info');
    }
  });
}

async function closeHandledTabs() {
  await perform(async () => {
    const handledIdentities = new Set();
    for (const app of state.applications) {
      if (!['submitted', 'withdrawn', 'rejected', 'interview', 'offer'].includes(app?.status)) continue;
      try { handledIdentities.add(normalizeJobUrl(app.url).identity); } catch { /* ignore invalid URL */ }
    }
    const tabs = await (chrome.tabs?.query?.({ currentWindow: true }) || []);
    const tabIdsToClose = [];
    const closedUrls = [];
    for (const tab of tabs) {
      if (tab?.pinned || !Number.isInteger(tab?.id) || !validWebUrl(tab?.url)) continue;
      try {
        if (handledIdentities.has(normalizeJobUrl(tab.url).identity)) {
          tabIdsToClose.push(tab.id);
          closedUrls.push(tab.url);
        }
      } catch { /* ignore unsupported URL */ }
    }
    if (!tabIdsToClose.length) {
      showNotice('No open tabs match a submitted or handled application.', 'info');
      return;
    }
    await chrome.tabs?.remove?.(tabIdsToClose);
    if (closedUrls.some(url => sameApplicationUrl(state.job.url, url))) {
      state.jobDrawerTouched = false;
      state.job = { title: '', company: '', url: '', notes: '', provider: '', supported: null };
      state.report = null;
    }
    await refreshActiveTab();
    renderJob(); renderReport(); await persistUi();
    showNotice(`Closed ${tabIdsToClose.length} handled job ${tabIdsToClose.length === 1 ? 'tab' : 'tabs'}.`);
  });
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
  $('#open-workspace').addEventListener('click', event => {
    event.preventDefault();
    void perform(async () => {
      await chrome.tabs.create({url: chrome.runtime.getURL('workspace.html')});
      showNotice('Career workspace opened in a new tab.');
    });
  });
  for (const button of $$('[data-tab]')) {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = ['apply', 'profile', 'applications', 'history'];
      const index = tabs.indexOf(state.tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
      switchTab(tabs[next], true);
    });
  }
  $('#job-summary-toggle')?.addEventListener('click', () => { state.jobDrawerTouched = true; });
  $('#job-details-drawer')?.addEventListener('toggle', updateJobDrawerPill);
  $('#job-form').addEventListener('submit', event => event.preventDefault());
  $('#job-form').addEventListener('input', () => {
    const oldUrl = state.job.url;
    state.jobDrawerTouched = true;
    state.job = readJob();
    if (oldUrl !== state.job.url) { state.job.supported = null; state.job.capturedAt = null; state.job.provider = ''; }
    const summaryParts = [state.job.title, state.job.company].filter(Boolean);
    if (state.job.notes) summaryParts.push('Note added');
    if ($('#job-summary-text')) $('#job-summary-text').textContent = summaryParts.length ? summaryParts.join(' · ') : 'Enter or capture role & company';
    renderPortalStatus(); queuePersistUi(); checkDuplicate();
  });
  $('#job-notes').addEventListener('input', () => {
    state.jobDrawerTouched = true;
    state.job = readJob();
    const summaryParts = [state.job.title, state.job.company].filter(Boolean);
    if (state.job.notes) summaryParts.push('Note added');
    if ($('#job-summary-text')) $('#job-summary-text').textContent = summaryParts.length ? summaryParts.join(' · ') : 'Enter or capture role & company';
    queuePersistUi();
  });
  $('#capture-job').addEventListener('click', () => perform(async () => {
    const job = await captureCurrentJob();
    const sameUrl = sameApplicationUrl(state.job.url, job.url);
    if (!sameUrl) { state.report = null; renderReport(); }
    state.activeTab = { id: state.activeTab?.id, url: job.url || '', title: job.title || '' };
    state.jobDrawerTouched = false;
    state.job = {
      title: job.title || '', company: job.company || '', url: job.url || '',
      notes: sameUrl ? $('#job-notes').value : '', provider: job.provider || '',
      supported: Boolean(job.supported), capturedAt: new Date().toISOString(),
      request_id: job.request_id || '', description: job.description || '',
    };
    renderJob(); await persistUi();
    showNotice('Job captured. Review the role and company before saving.', 'info');
  }));
  $('#prepare-application').addEventListener('click', () => prepareApplication());
  $('#inspect-form').addEventListener('click', () => runAction('inspect'));
  $('#fill-details').addEventListener('click', () => runAction('details'));
  $('#attach-resume').addEventListener('click', () => runAction('resume'));
  $('#fill-security-code').addEventListener('click', () => runAction('security-code'));
  $('#security-code-input').addEventListener('input', updateSecurityCodeVisibility);
  $('#security-code-input').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      runAction('security-code');
    }
  });
  chrome.tabs?.onActivated?.addListener(() => void refreshActiveTab());
  chrome.tabs?.onUpdated?.addListener(() => void refreshActiveTab());
  $('#clear-report').addEventListener('click', () => { if (state.busy) return; state.report = null; renderReport(); queuePersistUi(); });
  $('#report-problem').addEventListener('click', () => perform(async () => {
    await updateRun(state.report.diagnosticId, {reported: true, resolved: false});
    state.runs = await listRuns(); renderHistory(); renderReport(); switchTab('history');
    showNotice('Problem saved locally with this run. You can export it from History.');
  }));
  $('#history-filter').addEventListener('change', renderHistory);
  $('#refresh-history').addEventListener('click', () => perform(async () => { state.runs = await listRuns(); renderHistory(); }));
  $('#export-problems').addEventListener('click', () => downloadRuns(state.runs.filter(run => isProblem(run) && !run.resolved)));
  $('#submit-close-tab').addEventListener('click', () => submitAndCloseTab());
  $('#pass-close-tab').addEventListener('click', () => passAndCloseTab());
  $('#close-handled-tabs').addEventListener('click', () => closeHandledTabs());
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
      try {
        const metadata = await addResume(file, $('#resume-label').value.trim());
        state.resumes = await listResumes();
        state.selectedResumeId = metadata?.id || state.selectedResumeId;
        renderResumes(); await persistUi();
        $('#resume-label').value = '';
        showNotice('Résumé saved locally. It is ready to choose in Apply.');
      } finally { $('#resume-upload').value = ''; }
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
    const version=chrome.runtime.getManifest().version;
    $('#extension-version').textContent=version ? ` ${version}` : '';
    const [profile, resumes, applications, runs, stored] = await Promise.all([
      getProfile(), listResumes(), listApplications(), listRuns(), chrome.storage.local.get(UI_KEY),
    ]);
    state.profile = profile || {};
    state.resumes = Array.isArray(resumes) ? resumes : [];
    state.applications = Array.isArray(applications) ? applications : [];
    state.runs = Array.isArray(runs) ? runs : [];
    const ui = stored?.[UI_KEY];
    if (ui && typeof ui === 'object') {
      if (ui.job && typeof ui.job === 'object') state.job = { ...state.job, ...ui.job };
      state.report = ui.report && typeof ui.report === 'object' ? ui.report : null;
      if (state.report?.status === 'running') {
        state.report = {...state.report, status: 'interrupted',
          warnings: [...(Array.isArray(state.report.warnings) ? state.report.warnings : []),
            'The panel closed before this action finished. Its final result is unknown. Inspect the current page before retrying.']};
      }
      state.selectedResumeId = typeof ui.selectedResumeId === 'string' ? ui.selectedResumeId : '';
      if (['apply', 'profile', 'applications', 'history'].includes(ui.tab)) state.tab = ui.tab;
    }
    // Migrate the last pre-history report and recover checkpoints on reopening.
    // This stores evidence only; it never replays the employer-page action.
    if (state.report && (!state.report.diagnosticId || ui?.report?.status === 'running')) {
      if (!state.report.diagnosticId) state.report.legacy = true;
      state.report.diagnosticId ||= crypto.randomUUID();
      await checkpointRun(state.report.runAction || state.report.uiAction || 'inspect', state.job);
      await persistUi();
    }
    if (!state.selectedResumeId) state.selectedResumeId = state.profile.selected_resume_id || '';
    await refreshActiveTab();
    renderProfile(); renderResumes(); renderJob(); renderApplications(); renderHistory(); renderReport(); switchTab(state.tab);
  } catch (error) {
    showNotice(`Could not load your local workspace: ${friendlyError(error)}`, 'error');
  } finally { setBusy(false); }
}

import { setupLocalPanel } from './local-panel.js';
init().then(() => {
  setupLocalPanel();
  setupPrivacyPanel({perform, showNotice});
});
