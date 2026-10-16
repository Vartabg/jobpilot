import { normalizeJobUrl, workdayJob } from './model.js';

export const SUPPORTED_HOSTS = new Set([
  "boards.greenhouse.io", "job-boards.greenhouse.io", "boards-eu.greenhouse.io",
  "job-boards.eu.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com",
]);

export function isSupportedPage(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port
      && (SUPPORTED_HOSTS.has(url.hostname) || !!workdayJob(value));
  } catch { return false; }
}

export function providerFor(url) {
  try {
    if (workdayJob(url)) return 'Workday';
    const host = new URL(url).hostname;
    if (!SUPPORTED_HOSTS.has(host)) return "Manual";
    return host.includes("greenhouse") ? "Greenhouse" : host.includes("lever") ? "Lever" : "Ashby";
  } catch (_) { return "Manual"; }
}

async function activePage() {
  let tab;
  try { [tab] = await chrome.tabs.query({active: true, currentWindow: true}); }
  catch (_) { throw new Error('The application tab is unavailable. Open it and try again.'); }
  let url;
  try { url = new URL(tab?.url); } catch (_) { throw new Error("Open a job page, then click the JobPilot toolbar icon to grant page access."); }
  if (!Number.isInteger(tab?.id) || tab.id < 0 || !["https:", "http:"].includes(url.protocol)) throw new Error("Open an ordinary job webpage first.");
  if (/\.pdf$/i.test(url.pathname)) throw new Error('Open the job webpage rather than a PDF viewer before using JobPilot page actions.');
  if (tab.status === 'loading' || tab.pendingUrl && tab.pendingUrl !== url.href) throw new Error('The tab is still loading or navigating. Wait for the current page to finish, then try again.');
  return {tab, url};
}

async function unchangedPage(tabId, expectedUrl) {
  const current = await activePage();
  if (current.tab.id !== tabId || normalizeJobUrl(current.url.href).identity !== normalizeJobUrl(expectedUrl).identity) {
    throw new Error('The active tab or job changed during this action. Capture the current tab before retrying.');
  }
  if (workdayJob(expectedUrl) && current.url.href !== expectedUrl) {
    throw new Error('The Workday application step changed during this action. Inspect the current step before retrying.');
  }
  return current;
}

export async function captureCurrentJob() {
  const {tab, url} = await activePage();
  let results;
  try {
    results = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => {
      const jobs = [];
      const collect = object => {
        if (!object || typeof object !== "object") return;
        if (Array.isArray(object)) { object.forEach(collect); return; }
        if ([object["@type"]].flat().includes("JobPosting")) jobs.push(object);
        if (object["@graph"]) collect(object["@graph"]);
      };
      for (const script of document.querySelectorAll("script[type='application/ld+json']")) {
        try { collect(JSON.parse(script.textContent)); } catch (_) { /* Optional metadata. */ }
      }
      const job = jobs.length === 1 ? jobs[0] : null;
      const title = typeof job?.title === 'string' ? job.title.trim() : '';
      const description = typeof job?.description === 'string' ? job.description : '';
      const text = html => new DOMParser().parseFromString(String(html || ""), "text/html").body.textContent.trim();
      const workday = /^(?:[a-z0-9][a-z0-9-]*\.wd\d+\.myworkdayjobs\.com|wd\d+\.myworkdaysite\.com)$/.test(location.hostname);
      const publicDescription = document.querySelector('[data-automation-id="jobPostingDescription"]')?.innerText || '';
      return {
        pageUrl: location.href, documentType: document.contentType,
        title: (title || document.querySelector('[data-automation-id="jobPostingHeader"], [data-automation-id="jobTitleHeading"]')?.textContent?.trim() || document.querySelector("h1")?.textContent?.trim() || document.title || "").slice(0, 300),
        company: (typeof job?.hiringOrganization?.name === "string" ? job.hiringOrganization.name
          : location.hostname === 'jobs.ashbyhq.com' ? document.title.split(' @ ').slice(1).join(' @ ').trim() : '').slice(0,200),
        // A Workday application/review body may contain private answers.
        description: (description ? text(description) : publicDescription || (!workday ? document.querySelector("main, article, #content")?.innerText || "" : "")).slice(0, 40000),
        request_id: ['string', 'number'].includes(typeof job?.identifier?.value) ? String(job.identifier.value).slice(0, 200) : '',
      };
    }});
  } catch (_) { throw new Error("Page access expired. Click the JobPilot toolbar icon on this tab, then capture again."); }
  const captured = results?.find(item => item.frameId === 0)?.result;
  if (!captured || typeof captured !== 'object' || Array.isArray(captured) || typeof captured.pageUrl !== 'string') {
    throw new Error('This document did not return a capture result. Open the job webpage and capture again.');
  }
  if (captured.documentType && !['text/html', 'application/xhtml+xml'].includes(captured.documentType)) throw new Error('Capture works on job webpages. Open the job posting rather than its PDF or other file.');
  const current = await unchangedPage(tab.id, url.href);
  if (normalizeJobUrl(captured.pageUrl).identity !== normalizeJobUrl(current.url.href).identity) throw new Error('The job changed while it was being captured. Capture the current page again.');
  const {pageUrl, documentType, ...metadata} = captured;
  return {...metadata, title: captured.title || tab.title || "", url: pageUrl, provider: providerFor(pageUrl),
    supported: isSupportedPage(pageUrl)};
}

function frameFailure(frameId, error) {
  const detail = String(error?.message || error || '').slice(0, 300);
  return {
    frameId, status: 'interrupted', filled: [], skipped: [], failed: [], required: [],
    error: /frame|document|navigation|context|removed|closed|disconnected/i.test(detail)
      ? 'The form changed or disconnected before this frame returned its result. Inspect the current page before retrying.'
      : 'This frame could not return a result. Click the JobPilot icon on the application tab, then inspect the current page.',
  };
}

function disclosureProfile(profile) {
  const preferences = profile?.disclosure_preferences;
  if (!preferences || typeof preferences !== 'object' || Array.isArray(preferences)) return null;
  const veteran = preferences.veteran_status === undefined ? '' : preferences.veteran_status;
  const other = preferences.other_demographics === undefined ? '' : preferences.other_demographics;
  if (!['', 'protected_veteran', 'veteran', 'not_veteran', 'decline'].includes(veteran)
    || !['', 'decline'].includes(other) || !veteran && !other) return null;
  return {disclosure_preferences: {veteran_status: veteran, other_demographics: other}};
}

export async function runCurrentAction(action, profile, resume = null, {expectedUrl = '', onProgress} = {}) {
  if (!["inspect", "details", "resume"].includes(action)) throw new Error("Unknown application action");
  const {tab, url} = await activePage();
  if (!isSupportedPage(url.href)) {
    throw new Error("Autofill supports Greenhouse, Lever, Ashby, and Workday job applications. You can still capture and track this job. On Workday, open a job's application step and click the JobPilot toolbar icon.");
  }
  if (action === "resume" && !resume?.dataUrl) throw new Error("Choose a résumé with a local file first.");
  if (expectedUrl && normalizeJobUrl(expectedUrl).identity !== normalizeJobUrl(url.href).identity) {
    throw new Error("The current tab is a different job from your saved draft. Capture this tab before filling or attaching a résumé.");
  }
  const report = {pageUrl: url.href, frames: [], status: 'running', warnings: []};
  const disclosures = action === 'details' ? disclosureProfile(profile) : null;
  let discovered;
  try {
    await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, files: ["content.js"]});
    // Discover scope without sending profile data or file bytes. Chrome omits
    // inaccessible third-party frames from allFrames results (e.g. CAPTCHA).
    discovered = await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, func: () => {
      const visible = el => {
        if (!el?.getClientRects().length) return false;
        for (let node = el; node?.nodeType === 1; node = node.parentElement) {
          const style = getComputedStyle(node);
          if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        }
        return true;
      };
      const workday = /^(?:[a-z0-9][a-z0-9-]*\.wd\d+\.myworkdayjobs\.com|wd\d+\.myworkdaysite\.com)$/.test(location.hostname);
      const workdayScope = workday && typeof globalThis.__jobpilotWorkdayScope === 'function'
        ? globalThis.__jobpilotWorkdayScope() : null;
      const hasForm = workday ? !!workdayScope?.hasForm : [...document.querySelectorAll("input, textarea, select, [role='combobox']")].some(el => {
        if (el.disabled || ['hidden', 'search', 'submit', 'button', 'reset', 'password'].includes(el.type)) return false;
        const form = el.closest('form, [role="form"]') || el.form;
        const formName = [form?.getAttribute('aria-label'), ...(form?.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent),
          form?.querySelector(':scope > legend, :scope > h1, :scope > h2, :scope > h3')?.textContent].filter(Boolean).join(' ');
        if (el.closest('header, footer, nav, [role="search"], [role="banner"], [role="contentinfo"], [role="navigation"]')
          || /(?:^|[\s_-])(?:newsletter|subscribe|subscription|search|login|sign[\s_-]?in)(?:$|[\s_-])/i.test(`${form?.id || ''} ${form?.className || ''}`.replace(/([a-z])([A-Z])/g, '$1 $2'))
          || /\b(newsletter|subscribe|subscription)\b|^(?:search|log[ -]?in|sign[ -]?in)\b/i.test(formName)) return false;
        const shown = visible(el) || el.type === 'file' && visible(el.closest('.file-upload, .field, [class*="FileUpload"]') || el.parentElement);
        return shown && (el.closest('form, [role="form"], #application-form')
          || /first.?name|last.?name|email|resume|application/i.test([el.name, el.id, el.getAttribute('aria-label')].join(' ')));
      });
      return {pageUrl: location.href, hasForm, workdayScope, isTop: window === top,
        parentVisible: (() => {
          try {
            for (let frame = window; frame !== frame.top; frame = frame.parent) {
              if (!frame.frameElement) break; // Cross-origin scope is checked by top-page embed identity.
              if (!visible(frame.frameElement)) return false;
            }
            return true;
          } catch (_) { return false; }
        })(),
        visibleEmbeds: window === top ? [...document.querySelectorAll('iframe')].filter(visible).map(frame => frame.src).filter(Boolean) : []};
    }, args: [{probe: 'jobpilot-frames'}]});
  } catch (error) {
    report.frames.push(frameFailure(0, error));
    report.status = 'interrupted';
    return report;
  }
  const frames = [...new Map((discovered || []).filter(item => item.result && typeof item.result === 'object')
    .map(item => [item.documentId || item.frameId, item])).values()];
  const identity = value => {
    try {
      const page = new URL(value);
      return isSupportedPage(page.href) ? normalizeJobUrl(value).identity : '';
    } catch { return ''; }
  };
  const expectedIdentity = normalizeJobUrl(url.href).identity;
  const main = frames.find(frame => frame.frameId === 0 || frame.result.isTop);
  const visibleEmbeds = (Array.isArray(main?.result.visibleEmbeds) ? main.result.visibleEmbeds : []).map(identity).filter(Boolean);
  const candidates = frames.filter(frame => frame.result.hasForm
    && (!workdayJob(frame.result.pageUrl) || (action === 'inspect' ? true
      : action === 'details' ? (frame.result.workdayScope?.canFillDetails
        || disclosures && frame.result.workdayScope?.canFillDisclosures === true) : frame.result.workdayScope?.canAttachResume))
    && frame.result.parentVisible !== false && identity(frame.result.pageUrl) === expectedIdentity
    && (frame === main || visibleEmbeds.includes(identity(frame.result.pageUrl))));
  let targets = frames.filter(frame => identity(frame.result.pageUrl) === expectedIdentity);
  let ambiguous = false;
  const workdayMessage = workdayJob(url.href) ? main?.result.workdayScope?.message : '';
  if (action === 'inspect' && !candidates.length) {
    report.warnings.push(workdayMessage || 'No visible application form for this job was found. Open the application form, wait for it to load, then inspect again.');
  }
  if (action !== 'inspect') {
    ambiguous = candidates.length > 1;
    if (!candidates.length || ambiguous) {
      report.status = 'interrupted';
      report.warnings.push(ambiguous
        ? 'Several visible application forms match this job. Open the intended application in its own tab before autofilling or attaching a résumé.'
        : workdayMessage || 'A unique visible application form for this job could not be verified. Open the application form in its own tab, then inspect it again.');
      // Public inspection gives a review report without mutating an ambiguous
      // form. No profile or file bytes leave the panel in this branch.
      targets = frames.filter(frame => identity(frame.result.pageUrl) === expectedIdentity);
    } else targets = candidates;
  }
  // A result belongs to the document that was inspected, not to a replacement
  // document that happens to reuse its frame ID after navigation.
  for (const frame of targets) {
    const disclosureOnly = action === 'details' && !ambiguous && candidates.length > 0
      && !!workdayJob(frame.result.pageUrl) && !frame.result.workdayScope?.canFillDetails
      && frame.result.workdayScope?.canFillDisclosures === true;
    try { await unchangedPage(tab.id, url.href); }
    catch (error) {
      report.frames.push({...frameFailure(frame.frameId, error), error: error.message});
      break;
    }
    const target = frame.documentId
      ? {tabId: tab.id, documentIds: [frame.documentId]}
      : {tabId: tab.id, frameIds: [frame.frameId]};
    try {
      const results = await chrome.scripting.executeScript({target, func: async payload => {
        {
          const url = new URL(location.href), path = url.pathname.split('/').filter(Boolean);
          const host = url.hostname, jobIndex = path.indexOf('jobs');
          let identity;
          if (['boards.greenhouse.io', 'job-boards.greenhouse.io', 'boards-eu.greenhouse.io', 'job-boards.eu.greenhouse.io'].includes(host)) {
            const job = url.searchParams.get('gh_jid') || (jobIndex >= 0 ? path[jobIndex + 1] : '');
            if (job && /^\d+$/.test(job)) identity = `greenhouse:${path[0] || ''}:${job}`;
          } else if (['jobs.lever.co', 'jobs.eu.lever.co'].includes(host) && path.length >= 2) {
            identity = `lever:${path[0]}:${path[1]}`;
          } else if (host === 'jobs.ashbyhq.com' && path.length >= 2) {
            identity = `ashby:${path[0]}:${path[1]}`;
          }
          if (identity ? identity !== payload.expectedIdentity : location.href !== payload.expectedPageUrl) {
            return {status: 'interrupted', filled: [], skipped: [], failed: [], required: [],
              error: 'This tab changed to a different job before the action started. Capture the current tab before retrying.'};
          }
        }
        if (payload.expectedWorkdayStep) {
          const scope = globalThis.__jobpilotWorkdayScope?.();
          const allowed = payload.action === 'inspect' ? scope?.hasForm
            : payload.action === 'details' ? (payload.disclosureOnly ? scope?.canFillDisclosures === true : scope?.canFillDetails) : scope?.canAttachResume;
          if (scope?.stepKey !== payload.expectedWorkdayStep || !allowed) {
            return {status:'interrupted', filled:[], skipped:[], failed:[], required:[],
              error:'The Workday step changed or requires your input. Inspect the current step before retrying.'};
          }
        }
        if (typeof globalThis.__jobpilotRun !== "function") throw new Error("JobPilot could not inspect this form");
        try { return await globalThis.__jobpilotRun(payload); }
        catch (_) {
          // Never return form values in error diagnostics. Existing fields may
          // already have been filled, so this is an unknown, interrupted result.
          return {status: 'interrupted', filled: [], skipped: [], failed: [], required: [],
            error: 'This form stopped before returning a result. Inspect the page before retrying; existing answers will be preserved.'};
        }
      }, args: [{action: action === 'inspect' || ambiguous || !candidates.length ? 'inspect' : action,
        requireVisible: action !== 'inspect' && !ambiguous && candidates.length > 0,
        profile: action === 'details' && !ambiguous && candidates.length ? (disclosureOnly ? disclosures : profile) : {},
        disclosureOnly,
        resume: action === "resume" && !ambiguous && candidates.length ? resume : null,
        expectedWorkdayStep: frame.result.workdayScope?.stepKey || '',
        expectedIdentity: normalizeJobUrl(url.href).identity, expectedPageUrl: url.href}]});
      const result = results?.find(item => item.result && !Array.isArray(item.result)
        && Array.isArray(item.result.filled) && Array.isArray(item.result.required));
      report.frames.push(result ? {frameId: result.frameId ?? frame.frameId, ...result.result}
        : frameFailure(frame.frameId, 'No document result'));
    } catch (error) { report.frames.push(frameFailure(frame.frameId, error)); }
    try { await unchangedPage(tab.id, url.href); }
    catch (error) {
      const last = report.frames.at(-1);
      last.status = 'interrupted'; last.error = error.message;
    }
    // Checkpoints let the panel restore known frame results if it closes before
    // the next frame completes. They do not rerun any form mutation.
    if (onProgress) await onProgress({...report, frames: [...report.frames]});
  }
  if (!targets.length) report.frames.push(frameFailure(0, 'No document result'));
  const incomplete = report.frames.some(frame => frame.error || ['partial', 'interrupted'].includes(frame.status) || frame.failed?.length);
  const completed = report.frames.some(frame => !frame.error && frame.status !== 'interrupted');
  report.status = !candidates.length || ambiguous ? 'interrupted'
    : incomplete ? completed ? 'partial' : 'interrupted' : 'complete';
  return report;
}
