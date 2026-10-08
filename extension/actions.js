import { normalizeJobUrl } from './model.js';

export const SUPPORTED_HOSTS = new Set([
  "boards.greenhouse.io", "job-boards.greenhouse.io", "boards-eu.greenhouse.io",
  "job-boards.eu.greenhouse.io", "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com",
]);

export function providerFor(url) {
  try {
    const host = new URL(url).hostname;
    if (!SUPPORTED_HOSTS.has(host)) return "Manual";
    return host.includes("greenhouse") ? "Greenhouse" : host.includes("lever") ? "Lever" : "Ashby";
  } catch (_) { return "Manual"; }
}

async function activePage() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  let url;
  try { url = new URL(tab?.url); } catch (_) { throw new Error("Open a job page, then click the JobPilot toolbar icon to grant page access."); }
  if (!tab?.id || !["https:", "http:"].includes(url.protocol)) throw new Error("Open an ordinary job webpage first.");
  return {tab, url};
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
      const text = html => new DOMParser().parseFromString(String(html || ""), "text/html").body.textContent.trim();
      return {
        title: (job?.title || document.querySelector("h1")?.textContent?.trim() || document.title || "").slice(0, 300),
        company: (typeof job?.hiringOrganization?.name === "string" ? job.hiringOrganization.name
          : location.hostname === 'jobs.ashbyhq.com' ? document.title.split(' @ ').slice(1).join(' @ ').trim() : '').slice(0,200),
        description: (job?.description ? text(job.description) : (document.querySelector("main, article, #content")?.innerText || "")).slice(0, 40000),
        request_id: String(job?.identifier?.value || "").slice(0, 200),
      };
    }});
  } catch (_) { throw new Error("Page access expired. Click the JobPilot toolbar icon on this tab, then capture again."); }
  const captured = results?.[0]?.result || {};
  return {...captured, title: captured.title || tab.title || "", url: url.href, provider: providerFor(url.href),
    supported: url.protocol === "https:" && SUPPORTED_HOSTS.has(url.hostname)};
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

export async function runCurrentAction(action, profile, resume = null, {expectedUrl = '', onProgress} = {}) {
  if (!["inspect", "details", "resume"].includes(action)) throw new Error("Unknown application action");
  const {tab, url} = await activePage();
  if (url.protocol !== "https:" || !SUPPORTED_HOSTS.has(url.hostname)) {
    throw new Error("Autofill supports Greenhouse, Lever, and Ashby application hosts. You can still capture and track this job.");
  }
  if (action === "resume" && !resume?.dataUrl) throw new Error("Choose a résumé with a local file first.");
  if (expectedUrl && normalizeJobUrl(expectedUrl).identity !== normalizeJobUrl(url.href).identity) {
    throw new Error("The current tab is a different job from your saved draft. Capture this tab before filling or attaching a résumé.");
  }
  const report = {pageUrl: url.href, frames: [], status: 'running', warnings: []};
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
      const hasForm = [...document.querySelectorAll("input, textarea, select, [role='combobox']")].some(el => {
        if (el.disabled || ['hidden', 'search', 'submit', 'button', 'reset', 'password'].includes(el.type)) return false;
        const shown = visible(el) || el.type === 'file' && visible(el.closest('.file-upload, .field, [class*="FileUpload"]') || el.parentElement);
        return shown && (el.closest('form, [role="form"], #application-form')
          || /first.?name|last.?name|email|resume|application/i.test([el.name, el.id, el.getAttribute('aria-label')].join(' ')));
      });
      return {pageUrl: location.href, hasForm, isTop: window === top,
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
      return page.protocol === 'https:' && SUPPORTED_HOSTS.has(page.hostname) ? normalizeJobUrl(value).identity : '';
    } catch { return ''; }
  };
  const expectedIdentity = normalizeJobUrl(url.href).identity;
  const main = frames.find(frame => frame.frameId === 0 || frame.result.isTop);
  const visibleEmbeds = (Array.isArray(main?.result.visibleEmbeds) ? main.result.visibleEmbeds : []).map(identity).filter(Boolean);
  const candidates = frames.filter(frame => frame.result.hasForm && identity(frame.result.pageUrl) === expectedIdentity
    && (frame === main || visibleEmbeds.includes(identity(frame.result.pageUrl))));
  let targets = frames.filter(frame => identity(frame.result.pageUrl) === expectedIdentity);
  let ambiguous = false;
  if (action !== 'inspect') {
    ambiguous = candidates.length > 1;
    if (!candidates.length || ambiguous) {
      report.status = 'interrupted';
      report.warnings.push(ambiguous
        ? 'Several visible application forms match this job. Open the intended application in its own tab before autofilling or attaching a résumé.'
        : 'A unique visible application form for this job could not be verified. Open the application form in its own tab, then inspect it again.');
      // Public inspection gives a review report without mutating an ambiguous
      // form. No profile or file bytes leave the panel in this branch.
      targets = frames.filter(frame => identity(frame.result.pageUrl) === expectedIdentity);
    } else targets = candidates;
  }
  // A result belongs to the document that was inspected, not to a replacement
  // document that happens to reuse its frame ID after navigation.
  for (const frame of targets) {
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
        if (typeof globalThis.__jobpilotRun !== "function") throw new Error("JobPilot could not inspect this form");
        try { return await globalThis.__jobpilotRun(payload); }
        catch (_) {
          // Never return form values in error diagnostics. Existing fields may
          // already have been filled, so this is an unknown, interrupted result.
          return {status: 'interrupted', filled: [], skipped: [], failed: [], required: [],
            error: 'This form stopped before returning a result. Inspect the page before retrying; existing answers will be preserved.'};
        }
      }, args: [{action: action === 'inspect' || ambiguous || !candidates.length ? 'inspect' : action,
        profile: action === 'details' && !ambiguous && candidates.length ? profile : {},
        resume: action === "resume" && !ambiguous && candidates.length ? resume : null,
        expectedIdentity: normalizeJobUrl(url.href).identity, expectedPageUrl: url.href}]});
      const result = results?.find(item => item.result && typeof item.result === 'object');
      report.frames.push(result ? {frameId: result.frameId ?? frame.frameId, ...result.result}
        : frameFailure(frame.frameId, 'No document result'));
    } catch (error) { report.frames.push(frameFailure(frame.frameId, error)); }
    // Checkpoints let the panel restore known frame results if it closes before
    // the next frame completes. They do not rerun any form mutation.
    if (onProgress) await onProgress({...report, frames: [...report.frames]});
  }
  if (!targets.length) report.frames.push(frameFailure(0, 'No document result'));
  const incomplete = report.frames.some(frame => frame.error || ['partial', 'interrupted'].includes(frame.status) || frame.failed?.length);
  const completed = report.frames.some(frame => !frame.error && frame.status !== 'interrupted');
  report.status = action !== 'inspect' && (!candidates.length || ambiguous) ? 'interrupted'
    : incomplete ? completed ? 'partial' : 'interrupted' : 'complete';
  return report;
}
