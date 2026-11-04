import { normalizeJobUrl } from './model.js';

const GREENHOUSE = new Set(['boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards-eu.greenhouse.io', 'job-boards.eu.greenhouse.io']);
const LEVER = new Set(['jobs.lever.co', 'jobs.eu.lever.co']);
const MUTATIONS = new Set(['details', 'resume', 'answer', 'prepare-submit', 'submit']);
const PAGE_OPS = new Set(['inspect', ...MUTATIONS, 'receipt']);
const RESERVED = new Set(['api', 'jobs', 'search', 'login', 'signin', 'signup', 'account', 'auth', 'application', 'apply']);
const token = value => typeof value === 'string' && /^[a-z0-9][a-z0-9_-]{0,199}$/i.test(value);
const object = value => value && typeof value === 'object' && !Array.isArray(value);

// Host membership alone does not identify a particular application.
export function localJob(value) {
  if (typeof value !== 'string' || value.length > 5000) return null;
  try {
    const url = new URL(value), path = url.pathname.split('/').filter(Boolean);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || !token(path[0]) || RESERVED.has(path[0].toLowerCase())) return null;
    let provider;
    if (GREENHOUSE.has(url.hostname)) {
      const id = url.searchParams.get('gh_jid');
      const route = path.length === 3 && path[1] === 'jobs' && /^\d+$/.test(path[2]);
      if (!route && !(path.length === 1 && id && /^\d+$/.test(id))) return null;
      if (route && id && id !== path[2]) return null;
      provider = 'Greenhouse';
    } else if (LEVER.has(url.hostname) || url.hostname === 'jobs.ashbyhq.com') {
      const suffix = LEVER.has(url.hostname) ? 'apply' : 'application';
      if (!token(path[1]) || RESERVED.has(path[1].toLowerCase()) || !(path.length === 2 || path.length === 3 && path[2] === suffix)) return null;
      provider = suffix === 'apply' ? 'Lever' : 'Ashby';
    } else return null;
    return {url: url.href, identity: normalizeJobUrl(url.href).identity, provider};
  } catch { return null; }
}

function beforeDeadline(request) {
  if (!Number.isFinite(request.deadline) || request.deadline <= Date.now()) throw new Error('The request deadline expired. Inspect the page before trying a new action.');
}

async function checkedTab(request) {
  beforeDeadline(request);
  if (!Number.isInteger(request.tabId) || request.tabId < 0) throw new Error('An explicit application tabId is required.');
  const expected = localJob(request.expectedUrl);
  if (!expected) throw new Error('expectedUrl must identify a supported HTTPS ATS job.');
  let tab;
  try { tab = await chrome.tabs.get(request.tabId); }
  catch { throw new Error('The requested application tab is unavailable.'); }
  const current = localJob(tab?.url);
  if (tab?.id !== request.tabId || !current || current.identity !== expected.identity || current.url !== expected.url) {
    throw new Error('The requested tab or application URL changed. Inspect the intended page again.');
  }
  if (tab.status === 'loading' || tab.pendingUrl && tab.pendingUrl !== current.url) throw new Error('The application tab is navigating. Wait and inspect it again.');
  beforeDeadline(request);
  return current;
}

function pagePayload(request) {
  const payload = {op: request.op, expectedUrl: new URL(request.expectedUrl).href};
  if (MUTATIONS.has(request.op)) {
    if (typeof request.documentId !== 'string' || !request.documentId || request.documentId.length > 128) throw new Error('A documentId from inspection is required.');
    if (typeof request.snapshot !== 'string' || !request.snapshot || request.snapshot.length > 256) throw new Error('A fresh inspection snapshot is required.');
    payload.snapshot = request.snapshot;
  }
  if (request.op === 'details') {
    if (!object(request.profile)) throw new Error('Supply an explicit verified profile object.');
    // Demographic choices require the single-question decision gate.
    const {disclosure_preferences: ignored, ...profile} = request.profile;
    payload.profile = profile;
  } else if (request.op === 'resume') {
    const resume = request.resume;
    const match = typeof resume?.dataUrl === 'string' && resume.dataUrl.match(/^data:application\/pdf;base64,([A-Za-z0-9+/]*={0,2})$/);
    if (!object(resume) || !match || !/^JVBERi[0-3]/.test(match[1]) || match[1].length % 4 || match[1].length / 4 * 3 - (match[1].endsWith('==') ? 2 : match[1].endsWith('=') ? 1 : 0) > 512 * 1024) {
      throw new Error('Supply an explicit PDF résumé of at most 512 KiB.');
    }
    payload.resume = resume;
  } else if (request.op === 'answer') {
    if (typeof request.questionId !== 'string' || !request.questionId || request.questionId.length > 256 || !Object.hasOwn(request, 'value')) throw new Error('An explicit questionId and value are required.');
    if (!['string', 'boolean', 'number'].includes(typeof request.value) || typeof request.value === 'number' && !Number.isFinite(request.value)) throw new Error('The answer must be an explicit text, number or boolean value.');
    payload.questionId = request.questionId;
    payload.value = request.value;
    payload.replace = request.replace === true;
    payload.explicitPersonalDecision = request.explicitPersonalDecision === true;
  } else if (request.op === 'submit') {
    if (request.confirm !== true || typeof request.nonce !== 'string' || !request.nonce || request.nonce.length > 256) throw new Error('Submit requires a preparation nonce and confirm: true.');
    payload.nonce = request.nonce;
    payload.confirm = true;
  }
  return payload;
}

export async function routeLocalRequest(request) {
  beforeDeadline(request);
  if (request.op === 'tabs') {
    const tabs = await chrome.tabs.query({});
    return {tabs: tabs.flatMap(tab => {
      const job = localJob(tab.url);
      return Number.isInteger(tab.id) && job ? [{tabId: tab.id, ...job}] : [];
    })};
  }
  if (request.op === 'open') {
    const job = localJob(request.url);
    if (!job) throw new Error('Open requires a supported HTTPS ATS job URL.');
    beforeDeadline(request);
    const tab = await chrome.tabs.create({url: job.url, active: request.active === true});
    if (!Number.isInteger(tab?.id)) throw new Error('Chrome did not return the opened tab.');
    return {tabId: tab.id, ...job};
  }
  if (!PAGE_OPS.has(request.op)) throw new Error('Unknown local application operation.');
  const payload = pagePayload(request);
  await checkedTab(request);
  const hasDocument = typeof request.documentId === 'string' && request.documentId.length > 0;
  if (hasDocument && request.documentId.length > 128) throw new Error('Invalid documentId.');
  const target = hasDocument ? {tabId: request.tabId, documentIds: [request.documentId]} : {tabId: request.tabId, frameIds: [0]};
  let installed;
  try { installed = await chrome.scripting.executeScript({target, files: ['content.js', 'local-content.js']}); }
  catch (error) {
    const message=String(error?.message || '');
    if (/could not load file|cannot load file|failed to load.*script/i.test(message)) throw new Error('Chrome could not load JobPilot’s form scripts. Reload JobPilot in chrome://extensions, then reopen its side panel and Connect.');
    if (/cannot access contents|must request permission|not allowed to access/i.test(message)) throw new Error('Chrome has not granted access to this application. Click the JobPilot toolbar icon on this application tab, then inspect again.');
    throw new Error('The inspected document is unavailable or page access expired. Inspect again; no action was replayed.');
  }
  const main = installed?.find(frame => frame.frameId === 0 && typeof frame.documentId === 'string' && frame.documentId && (!hasDocument || frame.documentId === request.documentId));
  if (!main) throw new Error('Chrome did not confirm the top application document. Inspect again.');
  await checkedTab(request);
  let results;
  try {
    results = await chrome.scripting.executeScript({target: {tabId: request.tabId, documentIds: [main.documentId]}, func: async request => {
      if (window !== top || location.href !== request.expectedUrl || !['text/html', 'application/xhtml+xml'].includes(document.contentType)) throw new Error('The application document changed.');
      if (typeof globalThis.__jobpilotLocalRun !== 'function') throw new Error('The local controller is unavailable.');
      try { return await globalThis.__jobpilotLocalRun(request); }
      catch (error) {
        // The bounded local controller throws static hold diagnostics, never field values.
        return {localError: typeof error?.message === 'string' ? error.message.slice(0, 500) : 'The application requires manual review.'};
      }
    }, args: [payload]});
  } catch { throw new Error('The application action did not return a result. Its outcome is unknown; inspect before a new action. Nothing was replayed.'); }
  const result = results?.find(frame => frame.frameId === 0 && frame.documentId === main.documentId)?.result;
  if (!object(result)) throw new Error('The application action returned no verified document result. Its outcome is unknown.');
  if (typeof result.localError === 'string') throw new Error(result.localError || 'The application requires manual review.');
  if (request.op !== 'submit') await checkedTab(request);
  return {...result, tabId: request.tabId, documentId: main.documentId, pageUrl: payload.expectedUrl};
}
