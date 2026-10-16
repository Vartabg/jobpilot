// Diagnostics deliberately exclude input values, profile data, file names,
// bytes, page HTML and raw exceptions. Only known messages become reason codes.
export const RUN_LIMIT = 50;
export const REASONS = {
  manual_answer: 'Answer this question yourself',
  separate_resume: 'Use Attach résumé separately',
  existing_answer: 'Your existing answer was preserved',
  missing_profile: 'No saved profile value',
  missing_history: 'No saved history for this row',
  current_job: 'Current job: leave end date to you',
  date_format: 'Saved date does not match this field format',
  phone_format: 'This phone field has a separate country code. Review its format and enter it yourself.',
  workday_multiselect: 'This Workday multi-select needs a manual choice. Review it on the application page.',
  disclosure_manual: 'No saved disclosure choice applies to this question. Answer it yourself.',
  disclosure_choice: 'No unique offered choice matches your saved disclosure preference. Review this question yourself.',
  authorization_country: 'Authorization country does not match your saved profile',
  sponsorship_country: 'Sponsorship country does not match your saved profile',
  unmapped_question: 'No verified mapping for this question',
  history_mapping: 'History field needs review',
  history_context: 'History context is not clear',
  ambiguous_field: 'This field changed or is ambiguous. Review it manually.',
  rejected_match: 'The form did not retain a valid, unambiguous match. Review this field.',
  control_failed: 'This control could not be filled. Other supported fields were still checked.',
  changed_after_fill: 'The form changed after filling this field. Review its current value.',
  existing_search: 'An existing dropdown search was preserved. Choose its option on the page.',
  user_changed_search: 'Your dropdown answer or search changed during autofill and was preserved.',
  unsupported_frame: 'This frame is outside the supported application hosts',
  frame_changed: 'The form changed or disconnected before this frame returned its result. Inspect the current page before retrying.',
  frame_failed: 'This frame could not return a result. Click the JobPilot icon on the application tab, then inspect the current page.',
  page_changed: 'The application page changed. Review the fields already filled, then inspect the new page.',
  wrong_job: 'The current tab is a different job from your saved draft. Capture this tab before filling or attaching a résumé.',
  wrong_document: 'This tab changed to a different job before the action started. Capture the current tab before retrying.',
  form_exception: 'This form stopped before returning a result. Inspect the page before retrying; existing answers will be preserved.',
  timeout: 'The form kept changing or took too long. Review progress and retry remaining empty fields.',
  interrupted: 'Autofill was interrupted. Review the preserved progress and inspect the form before retrying.',
  panel_closed: 'The panel closed before this action finished. Its final result is unknown. Inspect the current page before retrying.',
  already_running: 'Autofill is already running on this page. Wait for its report before retrying.',
  unidentified_field: 'A field could not be identified. Review the page.',
  ambiguous_form: 'Several visible application forms match this job. Open the intended application in its own tab before autofilling or attaching a résumé.',
  missing_form: 'A unique visible application form for this job could not be verified. Open the application form in its own tab, then inspect it again.',
  workday_step: 'Only the current visible Workday step is supported. Review it and move to the next step yourself.',
  workday_signin: 'Complete Workday sign-in or account steps yourself, then inspect the visible application step.',
  workday_manual: 'Complete Workday questions, disclosures, availability and final review yourself.',
  workday_unverified: 'The current Workday application step could not be verified. Wait for it to load and inspect again.',
  workday_missing: 'Open one visible Workday application step before using autofill.',
  workday_changed: 'The Workday application step changed. Inspect the current step before retrying.',
  page_access: 'Open a job page, then click the JobPilot toolbar icon to grant page access.',
  ordinary_page: 'Open an ordinary job webpage first.',
  unsupported_host: 'Autofill supports Greenhouse, Lever, and Ashby application hosts. You can still capture and track this job.',
  missing_resume: 'Choose a résumé with a local file first.',
  unavailable_resume: 'This résumé is no longer in the library.',
  restored_resume: 'This backup includes résumé metadata only. Add the file again before attaching it.',
  resume_field: 'No unique résumé upload field found. Attach the file manually.',
  existing_resume: 'An existing attachment was preserved.',
  upload_error: 'The form reported an upload error. Review the page.',
  upload_accepted: 'The form displays this résumé attachment. Review it before submitting.',
  upload_unconfirmed: 'File selected; the form has not confirmed acceptance. Check the attachment on the page.',
  upload_failed: 'The file could not be attached. Upload it manually.',
  unknown: 'Unexpected error (raw details omitted)',
};
const codes = new Map(Object.entries(REASONS).map(([code, message]) => [message, code]));
const choices = (value, allowed, fallback) => allowed.includes(value) ? value : fallback;
const array = value => Array.isArray(value) ? value : [];
const code = value => codes.get(value) || (Object.hasOwn(REASONS, value) ? value : 'unknown');
const timestamp = (value, fallback) => Number.isNaN(Date.parse(value)) ? fallback : new Date(value).toISOString();
const TYPES = ['text', 'email', 'tel', 'url', 'number', 'date', 'month', 'textarea', 'select', 'combobox', 'radio', 'checkbox', 'choice', 'file'];

function label(value) {
  return typeof value === 'string' ? value.slice(0, 500)
    .replace(/https?:\/\/\S+|data:\S+/gi, '[link omitted]')
    .replace(/[\w.+-]+@[\w.-]+\.[a-z]{2,}/gi, '[email omitted]')
    .replace(/(?:\+?\d[\d ().-]*){7,}/g, '[number omitted]')
    .replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, 160) : '';
}

// Preserve public requisition identity only. Arbitrary paths, query tokens,
// fragments, credentials and unsupported-site paths never enter this log.
function jobUrl(value) {
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return {url: '', provider: 'Manual'};
    const path = url.pathname.split('/').filter(Boolean);
    let parts = [], provider = 'Manual';
    // Workday's application suffix can contain step/account state. Retain only
    // the bounded public candidate requisition, never that suffix or its query.
    const candidate = path.slice();
    if (/^[a-z]{2,3}(?:-[a-z0-9]{2,8}){1,2}$/i.test(candidate[0] || '')) candidate.shift();
    const workdayIndex = /^[a-z0-9][a-z0-9-]*\.wd\d+\.myworkdayjobs\.com$/.test(url.hostname) ? 1
      : /^wd\d+\.myworkdaysite\.com$/.test(url.hostname) && candidate[0] === 'recruiting' ? 3 : -1;
    if (url.protocol === 'https:' && !url.username && !url.password && !url.port
      && workdayIndex >= 0 && candidate[workdayIndex] === 'job') {
      const end = candidate.indexOf('apply', workdayIndex + 1);
      const publicParts = candidate.slice(0, end < 0 ? undefined : end);
      const requisition = publicParts.slice(workdayIndex + 1);
      if (requisition.length >= 1 && requisition.length <= 2
        && publicParts.every(part => /^[a-zA-Z0-9_-]{1,100}$/.test(part))) {
        parts = publicParts; provider = 'Workday';
      }
    }
    if (url.hostname === 'jobs.ashbyhq.com' && path.length >= 2) { parts = path.slice(0, 2); provider = 'Ashby'; }
    else if (['jobs.lever.co', 'jobs.eu.lever.co'].includes(url.hostname) && path.length >= 2) { parts = path.slice(0, 2); provider = 'Lever'; }
    else if (['boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards-eu.greenhouse.io', 'job-boards.eu.greenhouse.io'].includes(url.hostname)) {
      const job = url.searchParams.get('gh_jid') || path[path.indexOf('jobs') + 1];
      if (/^\d+$/.test(job || '')) { parts = [path[0], 'jobs', job]; provider = 'Greenhouse'; }
    }
    if (parts.some(part => !/^[a-zA-Z0-9_-]{1,100}$/.test(part))) parts = [];
    return {url: url.origin + (parts.length ? '/' + parts.join('/') : '/'), provider};
  } catch { return {url: '', provider: 'Manual'}; }
}

export function sanitizeRun(input, previous = null, version = '') {
  const report = input.report || {};
  const now = new Date().toISOString();
  let remaining = 200, truncated = false;
  const rows = value => {
    const source = array(value);
    const kept = source.slice(0, remaining);
    remaining -= kept.length;
    truncated ||= kept.length !== source.length;
    return kept.map(row => ({label: label(row?.label) || 'Unlabeled field',
      ...(TYPES.includes(row?.type) ? {type: row.type} : {}),
      ...(row?.reason ? {reason: code(row.reason)} : {})}));
  };
  const frames = array(report.frames).slice(0, 8).map(frame => ({
    frameId: Number.isInteger(frame?.frameId) ? frame.frameId : 0,
    status: choices(frame?.status, ['running', 'complete', 'partial', 'interrupted'], 'complete'),
    failed: rows(frame?.failed), required: rows(frame?.required), skipped: rows(frame?.skipped), filled: rows(frame?.filled),
    warnings: array(frame?.warnings).slice(0, 10).map(code),
    ...(frame?.error ? {error: code(frame.error)} : {}),
    resume: {status: choices(frame?.resume?.status, ['not-requested', 'accepted', 'prepared', 'failed', 'no-target', 'already-attached'], 'not-requested'),
      ...(frame?.resume?.message ? {reason: code(frame.resume.message)} : {})},
  }));
  const status = choices(report.status, ['running', 'complete', 'partial', 'interrupted'], 'interrupted');
  const warnings = array(report.warnings).slice(0, 10).map(code);
  const problem = Boolean(report.error || warnings.length || ['partial', 'interrupted'].includes(status) || frames.some(frame =>
    frame.error || frame.failed.length || frame.warnings.length || ['partial', 'interrupted'].includes(frame.status)
    || ['failed', 'no-target', 'prepared'].includes(frame.resume.status)
    || frame.skipped.some(row => ['unmapped_question', 'history_mapping', 'history_context', 'unknown'].includes(row.reason))));
  const manual = frames.some(frame => frame.required.length || frame.skipped.some(row => ['missing_profile', 'missing_history', 'manual_answer', 'date_format', 'phone_format', 'workday_multiselect', 'disclosure_manual', 'disclosure_choice'].includes(row.reason)));
  const page = jobUrl(report.pageUrl || input.job?.url);
  return {
    id: previous?.id || input.id, action: choices(input.action, ['inspect', 'details', 'resume'], 'inspect'),
    version: previous ? previous.version : input.legacy === true ? '' : (/^\d+(?:\.\d+){1,3}$/.test(version) ? version : ''),
    startedAt: previous?.startedAt || timestamp(input.startedAt, now), updatedAt: now,
    ...page, company: label(input.job?.company), title: label(input.job?.title),
    status, health: status === 'running' ? 'running' : problem ? 'problem' : manual ? 'manual' : 'ok',
    reported: previous?.reported === true, resolved: previous?.resolved === true,
    warnings, ...(report.error ? {error: code(report.error)} : {}), frames,
    truncated: truncated || array(report.frames).length > frames.length,
  };
}
