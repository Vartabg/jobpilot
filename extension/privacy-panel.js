import { scanCurrentPrivacy } from './privacy-action.js';
import { analyzeFields } from './privacy-analyzer.js';

const STORAGE_NAMESPACE = 'jobpilot_privacy_v1';
const $ = id => document.getElementById(id);
const labels = {
  required: 'Required response; no separate decline choice identified',
  optional: 'Optional',
  unknown: 'Requirement unclear',
  response_required_disclosure_optional: 'Response required; disclosure optional',
};
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function publicUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.origin + url.pathname : '';
  } catch { return ''; }
}

export function restorePrivacyReport(saved) {
  if (!saved || !Array.isArray(saved.fields) || saved.fields.length > 500) return null;
  const base = analyzeFields([], {source_url: publicUrl(saved.source_url), notices: (saved.notices || []).slice(0, 30)});
  const fields = [], findings = [];
  for (const raw of saved.fields) {
    const item = analyzeFields([raw]);
    const frame = Number.isInteger(raw.frame_id) && raw.frame_id >= 0
      ? {frame_id:raw.frame_id, frame_url:publicUrl(raw.frame_url)} : {};
    fields.push(...item.fields.map(field => ({...field, ...frame})));
    findings.push(...item.findings.map(finding => ({...finding, ...frame})));
  }
  const frames = (Array.isArray(saved.frames) ? saved.frames : []).slice(0, 8).filter(frame => Number.isInteger(frame?.frame_id) && frame.frame_id >= 0)
    .map(frame => ({frame_id:frame.frame_id, source_url:publicUrl(frame.source_url),
      field_count:Number.isInteger(frame.field_count) && frame.field_count >= 0 ? frame.field_count : 0,
      status:['inspected','hidden','unavailable'].includes(frame.status) ? frame.status : 'unavailable'}));
  return {...base, fields, findings, frames, field_count:fields.length,
    status:saved.status === 'inspected' ? 'inspected' : 'partial',
    checked_at:typeof saved.checked_at === 'string' && Number.isFinite(Date.parse(saved.checked_at)) ? saved.checked_at : ''};
}

export async function setupPrivacyPanel({perform, showNotice}) {
  let report = null;
  function render() {
    $('privacy-result').hidden = !report;
    $('privacy-export').disabled = !report;
    $('privacy-clear').disabled = !report;
    if (!report) return;
    const findings = report.findings || [];
    $('privacy-context').textContent = [publicUrl(report.source_url),
      report.checked_at ? new Date(report.checked_at).toLocaleString() : ''].filter(Boolean).join(' · ');
    $('privacy-count').textContent = `${report.field_count} field labels inspected · ${findings.length} flagged requests. Coverage is incomplete.`;
    const rows = $('privacy-findings'); rows.replaceChildren();
    const selected = $('privacy-filter').value === 'required'
      ? findings.filter(finding => finding.optionality === 'required') : findings;
    if (!selected.length) rows.append(node('p', findings.length
      ? 'No requests match this filter. Review all requests for optional questions and unclear requirements.'
      : 'No flagged requests found in the inspected fields. Later steps or hidden questions may still request personal information.', 'fine-print'));
    for (const finding of selected) {
      const card = node('section', undefined, 'privacy-finding');
      card.append(node('h4', finding.label), node('p', labels[finding.optionality] || labels.unknown, 'privacy-requirement'));
      card.append(node('p', String(finding.category).replaceAll('_', ' '), 'fine-print'));
      card.append(node('p', finding.explanation));
      if (Number.isInteger(finding.frame_id)) card.append(node('p',
        `${finding.frame_id === 0 ? 'Main application page' : 'Embedded application frame'}: ${publicUrl(finding.frame_url) || 'Source not identified'}`, 'fine-print'));
      card.append(node('p', `Employer-provided help or purpose: ${finding.employer_stated_purpose || 'No specific purpose stated in the inspected field.'}`));
      if (finding.options?.length) {
        const details = node('details'); details.append(node('summary', 'Choices shown'));
        const choices = node('ul');
        for (const choice of finding.options) choices.append(node('li', choice));
        details.append(choices); card.append(details);
      }
      rows.append(card);
    }
    const notices = $('privacy-notices'); notices.replaceChildren();
    for (const notice of report.notices || []) notices.append(node('li', notice));
  }
  $('scan-privacy').addEventListener('click', async () => {
    await perform(async () => {
      report = null; render();
      $('privacy-status').textContent = 'Inspecting visible question labels. No answers are read or sent.';
      try {
        report = {...await scanCurrentPrivacy(), checked_at: new Date().toISOString()};
        try {
          await chrome.storage.local.set({[STORAGE_NAMESPACE]: report});
          $('privacy-status').textContent = 'Scan saved locally. Review the requests and coverage limits below.';
          showNotice('Privacy scan finished. Nothing was filled or submitted.');
        } catch {
          $('privacy-status').textContent = 'Scan finished, but it could not be saved locally. The current result is available below; export it before closing.';
          showNotice('Privacy scan finished. Local saving is unavailable; export this result if you want to keep it.', 'warning');
        }
      } catch (error) {
        $('privacy-status').textContent = 'The privacy scan could not finish. Open the application page, click the JobPilot toolbar icon, and try again.';
        throw error;
      }
    });
    render();
    if (report) $('privacy-heading').focus();
  });
  $('privacy-filter').addEventListener('change', render);
  $('privacy-clear').addEventListener('click', () => perform(async () => {
    await chrome.storage.local.remove(STORAGE_NAMESPACE);
    report = null; render();
    $('privacy-status').textContent = 'Saved scan cleared. Scan another application step when ready.';
  }).then(render));
  $('privacy-export').addEventListener('click', () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:'application/json'}));
    const link = node('a'); link.href = url; link.download = 'jobpilot-privacy-scan.json';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('privacy-status').textContent = 'Report exported. It contains question metadata and coverage limits.';
  });
  try {
    const saved = (await chrome.storage.local.get(STORAGE_NAMESPACE))[STORAGE_NAMESPACE];
    if (saved && Array.isArray(saved.fields)) {
      report = restorePrivacyReport(saved);
      $('privacy-status').textContent = 'Showing your last saved scan. Rescan the current application step for fresh results.';
    }
  } catch {
    $('privacy-status').textContent = 'The saved scan could not load. You can scan the current application again.';
  }
  render();
  $('scan-privacy').disabled = false;
}
