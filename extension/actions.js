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
        company: typeof job?.hiringOrganization?.name === "string" ? job.hiringOrganization.name.slice(0, 200) : "",
        description: (job?.description ? text(job.description) : (document.querySelector("main, article, #content")?.innerText || "")).slice(0, 40000),
        request_id: String(job?.identifier?.value || "").slice(0, 200),
      };
    }});
  } catch (_) { throw new Error("Page access expired. Click the JobPilot toolbar icon on this tab, then capture again."); }
  const captured = results?.[0]?.result || {};
  return {...captured, title: captured.title || tab.title || "", url: url.href, provider: providerFor(url.href),
    supported: url.protocol === "https:" && SUPPORTED_HOSTS.has(url.hostname)};
}

export async function runCurrentAction(action, profile, resume = null, {expectedUrl = ''} = {}) {
  if (!["inspect", "details", "resume"].includes(action)) throw new Error("Unknown application action");
  const {tab, url} = await activePage();
  if (url.protocol !== "https:" || !SUPPORTED_HOSTS.has(url.hostname)) {
    throw new Error("Autofill supports Greenhouse, Lever, and Ashby application hosts. You can still capture and track this job.");
  }
  if (action === "resume" && !resume?.dataUrl) throw new Error("Choose a résumé with a local file first.");
  if (expectedUrl && normalizeJobUrl(expectedUrl).identity !== normalizeJobUrl(url.href).identity) {
    throw new Error("The current tab is a different job from your saved draft. Capture this tab before filling or attaching a résumé.");
  }
  const target = {tabId: tab.id, allFrames: true};
  try {
    await chrome.scripting.executeScript({target, files: ["content.js"]});
    const results = await chrome.scripting.executeScript({target, func: async payload => {
      if (typeof globalThis.__jobpilotRun !== "function") throw new Error("JobPilot could not inspect this form");
      return await globalThis.__jobpilotRun(payload);
    }, args: [{action, profile, resume: action === "resume" ? resume : null}]});
    return {pageUrl: url.href, frames: results.filter(item => item.result).map(({frameId, result}) => ({frameId, ...result}))};
  } catch (_) { throw new Error("This page could not be accessed. Refresh it and click the JobPilot icon, then try again."); }
}
