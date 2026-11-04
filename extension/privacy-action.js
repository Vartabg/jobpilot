import {analyzeFields, publicPageUrl} from './privacy-analyzer.js';

const MAX_FRAMES = 8;
const MAX_FIELDS = 500;
const HTML_TYPES = new Set(['text/html', 'application/xhtml+xml']);

async function activePage() {
  let tab;
  try { [tab] = await chrome.tabs.query({active: true, currentWindow: true}); }
  catch { throw new Error('The application tab is unavailable. Open it and try again.'); }
  let url;
  try { url = new URL(tab?.url); }
  catch { throw new Error('Open the application webpage, then click the JobPilot toolbar icon to grant page access.'); }
  if (!Number.isInteger(tab?.id) || tab.id < 0 || !['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Open an ordinary application webpage before scanning its questions.');
  }
  if (/\.pdf$/i.test(url.pathname)) throw new Error('Open the application webpage rather than a PDF viewer before scanning.');
  if (tab.status === 'loading' || tab.pendingUrl && tab.pendingUrl !== url.href) {
    throw new Error('The tab is still loading or navigating. Wait for the application page to finish, then scan again.');
  }
  return {id: tab.id, url: url.href};
}

async function unchangedPage(expected) {
  const current = await activePage();
  if (current.id !== expected.id || current.url !== expected.url) {
    throw new Error('The active tab or application step changed during the privacy scan. Open the intended page and scan again.');
  }
}

function frameNotice(frameId) {
  return `Frame ${frameId} could not be inspected. Its questions are unknown; open the embedded application directly and click the JobPilot toolbar icon to scan it.`;
}

/** Read only visible field metadata under the existing toolbar activeTab grant.
 * No applicant profile, résumé, answers, storage, network or form actions enter
 * this flow. Document IDs bind every capture to its original inspected page.
 */
export async function scanCurrentPrivacy() {
  const page = await activePage();
  let injected;
  try {
    // Installation only defines an isolated-world function and reads public
    // document metadata. It does not install autofill or touch the page's DOM.
    injected = await chrome.scripting.executeScript({target: {tabId: page.id, allFrames: true}, files: ['privacy-content.js']});
  } catch {
    throw new Error('Page access is unavailable. Click the JobPilot toolbar icon on this application tab, then scan again. No questions were inspected.');
  }
  await unchangedPage(page);
  const discovered = [...new Map((Array.isArray(injected) ? injected : [])
    .filter(item => item && Number.isInteger(item.frameId))
    .map(item => [item.documentId || `missing:${item.frameId}`, item])).values()]
    .sort((a, b) => a.frameId === 0 ? -1 : b.frameId === 0 ? 1 : a.frameId - b.frameId);
  const notices = ['This scan reads question labels, required markers, offered choices and help text locally. It does not read entered answers, fill fields, submit an application, or contact a service.'];
  if (discovered.length > MAX_FRAMES) notices.push('The scan stopped at eight accessible frames. Other embedded questions are unknown.');
  if (!discovered.length) throw new Error('No readable webpage document was found. Open the application directly, click the JobPilot toolbar icon, then scan again.');
  const fields = [], findings = [], frames = [], scanNotices = [];
  let successes = 0, limited = discovered.length > MAX_FRAMES;
  for (const frame of discovered.slice(0, MAX_FRAMES)) {
    await unchangedPage(page);
    const source = publicPageUrl(frame.result?.source_url || '');
    const record = {frame_id: frame.frameId, document_id: frame.documentId || '', source_url: source, field_count: 0, status: 'unavailable'};
    frames.push(record);
    // A frame ID can be reused by a replacement document. Never fall back to
    // frameIds when Chrome did not return a document ID for this injection.
    if (fields.length >= MAX_FIELDS) {
      limited = true;
      scanNotices.push('The scan stopped at 500 fields across all frames. Other questions are unknown; inspect each application frame directly.');
      continue;
    }
    if (!frame.documentId || !HTML_TYPES.has(frame.result?.document_type)) {
      scanNotices.push(frameNotice(frame.frameId));
      continue;
    }
    try {
      const captures = await chrome.scripting.executeScript({target: {tabId: page.id, documentIds: [frame.documentId]}, func: () => {
        const visible = element => {
          if (!element || !element.getClientRects().length) return false;
          const style = getComputedStyle(element);
          return style.display !== 'none' && style.visibility !== 'hidden';
        };
        // Hidden same-origin embeddings are not part of the visible form.
        // Cross-origin parent visibility cannot be proved; flag that limit.
        let parentVisibility = 'visible';
        try {
          for (let current = window; current !== current.top; current = current.parent) {
            if (!current.frameElement) { parentVisibility = 'unknown'; break; }
            if (!visible(current.frameElement)) return {hidden: true};
          }
        } catch { parentVisibility = 'unknown'; }
        if (typeof globalThis.__jobpilotPrivacyCapture !== 'function') return {unavailable: true};
        return {payload: globalThis.__jobpilotPrivacyCapture(), document_type: document.contentType,
          parent_visibility: parentVisibility,
          visible_embeds: [...document.querySelectorAll('iframe')].filter(visible).length};
      }});
      const captured = captures?.find(item => item.documentId === frame.documentId && item.frameId === frame.frameId)?.result;
      if (captured?.hidden) { record.status = 'hidden'; continue; }
      if (!captured?.payload || !HTML_TYPES.has(captured.document_type) || publicPageUrl(captured.payload.source_url) !== source) {
        throw new Error('No matching document metadata');
      }
      if (!Array.isArray(captured.payload.fields) || captured.payload.fields.length > MAX_FIELDS) throw new Error('Invalid field metadata');
      const remaining = MAX_FIELDS - fields.length;
      if (captured.payload.fields.length > remaining) {
        limited = true;
        scanNotices.push('The scan stopped at 500 fields across all frames. Other questions are unknown; inspect each application frame directly.');
      }
      const report = analyzeFields(captured.payload.fields.slice(0, remaining), {source_url: source, coverage: 'visible_form', notices: captured.payload.notices});
      const provenance = {frame_id: frame.frameId, frame_url: source};
      fields.push(...report.fields.map(field => ({...field, ...provenance})));
      findings.push(...report.findings.map(finding => ({...finding, ...provenance})));
      scanNotices.push(...report.notices);
      if (captured.parent_visibility === 'unknown') scanNotices.push(`Frame ${frame.frameId}'s embedding visibility could not be verified. Its findings have separate frame provenance; review whether this frame belongs to the application.`);
      if (captured.visible_embeds > 0) scanNotices.push('The page contains embedded frames. Chrome may deny access to some of them; questions in inaccessible frames remain unknown. Open the application frame directly to inspect it.');
      record.status = 'inspected'; record.field_count = report.field_count;
      successes += 1;
    } catch {
      // Do not echo browser/page errors: they can contain URLs or form data.
      scanNotices.push(frameNotice(frame.frameId));
    }
  }
  await unchangedPage(page);
  if (!successes) throw new Error('No visible application fields could be inspected. Open the application webpage directly, click the JobPilot toolbar icon and scan again. Coverage is incomplete.');
  if (!fields.length) scanNotices.push('No visible question metadata was found. This does not establish that the application asks for no personal information. Open the form, sign in if necessary, and rescan each step.');
  const base = analyzeFields([], {source_url: page.url});
  return {...base, fields, findings, field_count: fields.length, frames,
    status: frames.some(frame => frame.status === 'unavailable') || limited ? 'partial' : 'inspected',
    notices: [...new Set([...notices, ...scanNotices, ...base.notices])], complete: false};
}
